"""Delay-first, resource-costed cut mapping and exact GW5A cofactor extraction."""
import json
from pathlib import Path
from constrained_mapping import constraints,annotate,verify_annotations,logic_only
from gowin_blif import read_blif
from gw5a_functional_map import lower,blif,permute_lut4_pins
from run_abc_reproduction import ROOT,invoke,cec,sha


def run(input_path,constraint_path,model_path,out,recipe='wide',defer_cec=False):
    if recipe not in ('wide','lut4','wide_no_recovery','lut4_local_wide','lut4_pins','wide_pins','local_wide_pins'):raise ValueError('Unknown architecture recipe')
    out.mkdir(parents=True,exist_ok=False)
    model=json.loads(model_path.read_text());delay=model['lut_envelope_ps']
    physical_cost_path=ROOT/'configs/gw5a_arch_cost_v2.json'
    physical_cost=json.loads(physical_cost_path.read_text());wire=physical_cost['cut_wire_ps'];wire_units=wire/delay
    binary=ROOT/'build/abc/_TEST/abc.exe'
    def proof(reference,candidate,log):
        if defer_cec:
            return dict(status='deferred_to_actual_JSON',equivalent=None,
                        reason='Resource mapper must prove its final actual JSON readback against the original source boundary')
        value=cec(binary,reference,candidate,log,300)
        if not value['equivalent']:raise RuntimeError('Architecture transformation failed CEC: '+str(candidate))
        return value
    source=out/'source.blif';source.write_text(logic_only(input_path.read_text()))
    original=read_blif(source,max_inputs=6)
    arrivals,required=constraints(json.loads(constraint_path.read_text()),original)
    # Omit output deadlines in the optimization copy: ABC then recovers area
    # under the initial global minimum delay. Per-output zero deadlines would
    # overconstrain every noncritical endpoint and unnecessarily inflate area.
    # User deadlines remain intact for independent acceptance and native P&R.
    zero_arrivals=all(v==0 for v in arrivals.values())
    objective={} if zero_arrivals else required
    # The flat BLIF timing reader allocates zero output deadlines even when
    # only arrivals are supplied. For a zero-arrival problem use a plain read,
    # which really leaves ABC's timing manager absent and enables global-delay
    # area recovery. Nonzero arrivals retain the real explicit requirements.
    preprocessing='&get -n; &syn2 -d -R 0; &b -d; &put; &get -n; &syn2 -d -R 0; &b -d; &put'
    rewritten=out/'rewritten.blif'
    rewrite_invocation,_=invoke(binary,f'read "{source.as_posix()}"; strash; {preprocessing}; write_blif "{rewritten.as_posix()}"; echo NEXTGOWIN_DONE',out/'rewriting.log',300)
    if rewrite_invocation['status']!='ok' or not rewritten.exists():raise RuntimeError('Delay-oriented graph rewriting failed')
    # GIA optimization can drop CI/CO names, but not their positional contract.
    # Prove that contract before rebinding names; then apply timing annotations
    # after the GIA roundtrip so the cut mapper actually sees them.
    rewrite_proof=proof(source,rewritten,out/'rewriting_cec.log')
    rewritten_network=read_blif(rewritten,max_inputs=6)
    if len(original['inputs'])!=len(rewritten_network['inputs']) or len(original['outputs'])!=len(rewritten_network['outputs']):
        raise ValueError('Graph rewriting changed interface width')
    names=dict(zip(rewritten_network['inputs'],original['inputs']))
    names.update(zip(rewritten_network['outputs'],original['outputs']))
    occupied=set(original['inputs']+original['outputs'])
    for i,n in enumerate(rewritten_network['nodes']):
        if n['output'] not in names:
            name='ng_rewritten_'+str(i)
            while name in occupied:name+='x'
            names[n['output']]=name;occupied.add(name)
    rebound=dict(inputs=original['inputs'],outputs=original['outputs'],nodes=[dict(inputs=[names[b] for b in n['inputs']],output=names[n['output']],truth=n['truth']) for n in rewritten_network['nodes']])
    rebound_text=blif(rebound)
    annotated=out/'annotated.blif';annotated.write_text(rebound_text if zero_arrivals else annotate(rebound_text,arrivals,objective,delay))
    library=out/'gw5a.lut'
    rows=[]
    compact=recipe in ('lut4','lut4_pins','lut4_local_wide','local_wide_pins')
    local_wide=recipe in ('lut4_local_wide','local_wide_pins')
    for k in range(1,5 if compact else 7):
        pins=[1.0]*k if k<=4 else [0.5]+[1.4]*4 if k==5 else [0.5,0.9]+[1.8]*4
        rows.append(str(k)+' '+str(1 if k<=4 else 2 if k==5 else 4)+' '+' '.join(format(t+wire_units,'.8f') for t in pins))
    library.write_text('# size LUT4-slots sorted intrinsic+external-wire costs\n'+'\n'.join(rows)+'\n')
    wide=out/'wide.blif'
    reader='read' if zero_arrivals else 'read_blif -n'
    command=f'read_lut "{library.as_posix()}"; print_lut; {reader} "{annotated.as_posix()}"; strash; dch; if -C 64 -F 1 -A 2; print_stats; write_blif "{wide.as_posix()}"; echo NEXTGOWIN_DONE'
    invocation,log=invoke(binary,command,out/'mapping.log',300)
    if invocation['status']!='ok' or not wide.exists():raise RuntimeError('Architecture cut mapping failed')
    annotation_ok=True if zero_arrivals else verify_annotations(wide.read_text(),arrivals,objective,delay)
    logic=out/'wide_logic.blif';logic.write_text(logic_only(wide.read_text()))
    cut_proof=proof(source,logic,out/'wide_cec.log')
    from gw5a_cut_choices import optimize
    recovered,recovery=optimize(read_blif(logic,max_inputs=6),arrivals,wire_ps=wire,lut_ps=delay,
        mux_data_ps=physical_cost['mux_data_ps'],mux_select_ps=physical_cost['mux_select_ps'],
        max_cuts=64 if local_wide else 8 if recipe in ('wide','wide_pins') else 1,
        max_window_nodes=8 if local_wide else 5,area_allowance=0.10 if local_wide else 0.02)
    recovery_path=out/'cut_recovery.json';recovery_path.write_text(json.dumps(recovery,indent=2)+'\n')
    recovered_path=out/'recovered_logic.blif';recovered_path.write_text(blif(recovered))
    recovery_proof=proof(source,recovered_path,out/'recovery_cec.log')
    network,record=lower(recovered,arrivals,lut_ps=delay,cut_wire_ps=wire,
        mux_data_ps=physical_cost['mux_data_ps'],mux_select_ps=physical_cost['mux_select_ps'])
    if recipe.endswith('_pins'):
        pins=model['physical_lut_inputs']
        if [p['internal_pin'] for p in pins]!=list('ABCD'):raise ValueError('Unknown LUT pin model order')
        network,pin_record=permute_lut4_pins(network,arrivals,[p['setup_max_ns']*1000 for p in pins],wire,delay,
            physical_cost['mux_data_ps'],physical_cost['mux_select_ps'])
        record['pin_assignment']=pin_record
        record['output_arrivals_ps']=pin_record['output_arrivals_ps']
    (out/'architecture.json').write_text(json.dumps(record,indent=2)+'\n')
    (out/'network.json').write_text(json.dumps(network)+'\n')
    target=out/'mapped_logic.blif';target.write_text(blif(network))
    final_proof=proof(source,target,out/'cofactor_cec.log')
    outputs={p:dict(arrival_ps=record['output_arrivals_ps'][p],required_ps=required[p],
                    slack_ps=required[p]-record['output_arrivals_ps'][p],path_lut_levels={},critical_path=[]) for p in required}
    timing=dict(outputs=outputs,wns_ps=min(v['slack_ps'] for v in outputs.values()),
                tns_ps=sum(min(0,v['slack_ps']) for v in outputs.values()),input_required_ps={},
                all_input_path_lengths_included=False,meets_required=all(v['slack_ps']>=-1e-6 for v in outputs.values()))
    result=dict(schema='gw5a-architecture-mapping-v1',strategy='arch_delay',architecture_recipe=recipe,
                input_sha256=sha(input_path),constraint_sha256=sha(constraint_path),model_sha256=sha(model_path),
                objective='Initial global minimum delay before area recovery; original user budgets used for independent acceptance',
                timing_scope='Analytical cell costs. Native SDC/P&R required for physical conclusions.',
                preprocessing=preprocessing,rewriting=rewrite_invocation,rewriting_cec=rewrite_proof,
                timing_encoding='Plain BLIF with default zero arrivals' if zero_arrivals else 'Explicit arrival/required annotations',
                physical_cost_sha256=sha(physical_cost_path),
                mapping=invocation,cut_recovery_cec=recovery_proof,cut_recovery_sha256=sha(recovery_path),annotations_preserved=annotation_ok,optimization_required_ps=objective,
                original_required_ps=required,cec=final_proof,proof_deferred_to_json=defer_cec,wide_cec=cut_proof,independent_timing=timing,
                native_relaxed_deadlines='Required times are exceeded' in log,architecture_sha256=sha(out/'architecture.json'))
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    return result,network
