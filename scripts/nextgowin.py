"""Gowin frontend -> open CPU synthesis/ACD mapping -> native P&R/bitstream."""
import argparse
import fnmatch
import json
from pathlib import Path
import subprocess
import sys
import time
from export_gowin_frontend import export
from analyze_route_fanout import analyze as route_fanout
from gowin_report import summaries
from gowin_netlist_format import complete_output_ports,normalize,legalize_gw5a_aliases,bind_net_aliases,name_unaliased_bits
from insert_gowin_pads import insert
from map_resource_json import map_design
from resource_preflight import count
from run_abc_reproduction import ROOT, sha
from verific_bridge import convert,units
from yosys_runtime import run, quote


def infer(bridge, out, top, keep_hierarchy=(), resource_optimizations=True, soft_comparisons=False, register_alternatives=False,auto_hierarchy=False,pack_dsp_registers=False,pack_dsp_inverted_enable=False):
    out.mkdir(parents=True, exist_ok=False)
    text=(bridge/'design.v').read_text(encoding='utf-8')
    dsp_library=[];verified_multipliers=[]
    if resource_optimizations:
        from gw5a_dsp import prepare,primitive_library
        from recover_small_memory import recover
        text,memories=recover(text,out)
        (out/'memory_recovery.json').write_text(json.dumps(memories,indent=2)+'\n')
        text,record,_,_=prepare(text)
        verified_multipliers=[r['module'] for r in record['multipliers']]
        (out/'dsp_map.json').write_text(json.dumps(record,indent=2)+'\n')
        (out/'dsp_cells.v').write_text(primitive_library())
        dsp_library=['read_verilog -overwrite -lib '+quote(out/'dsp_cells.v')]
    from hierarchy_islands import detect
    islands=detect(text,verified_multipliers,min_multipliers=0 if auto_hierarchy=='stateful' else 2) if auto_hierarchy else []
    automatic={r['module'] for r in islands};keep=[]
    for name,(a,b) in reversed(list(units(text).items())):
        if name in automatic or any(fnmatch.fnmatchcase(name.lstrip('\\'),pattern) for pattern in keep_hierarchy):
            text=text[:a]+'(* keep_hierarchy = 1 *)\n'+text[a:];keep.append(name)
    source=out/'hierarchy_input.v';source.write_text(text,encoding='utf-8')
    (out/'hierarchy.json').write_text(json.dumps(dict(keep_patterns=keep_hierarchy,kept_modules=keep,automatic_dsp_islands=islands,automatic_policy=auto_hierarchy,
        policy='Flatten ordinary logic for cross-module constant propagation; retain selected IP and SDC hierarchy.'),indent=2)+'\n')
    common = f'-top {top} -family gw5a -strict-gw5a-dffs -noflatten -nowidelut -noiopads'
    commands=[
        'read_verilog -lib +/gowin/cells_sim.v',
        'read_verilog -lib +/gowin/cells_xtra_gw5a.v',
        'read_verilog -overwrite -lib '+quote(bridge/'vendor_cells.v'),
        *dsp_library,
        'read_verilog -sv '+quote(source),
        'hierarchy -check -top '+top,
        'flatten',
        'hierarchy -check -top '+top,
        'select *',
        # ScriptPass ranges exclude their end label.
        'synth_gowin '+common+' -run coarse:map_gates',
        'write_rtlil '+quote(out/'inferred.il'),
    ]
    tail=[
        'synth_gowin '+common+' -run map_gates:map_luts',
        'hierarchy -check -top '+top,
        'check -assert',
        'blackbox =A:whitebox',
        'check -assert -noinit',
        'write_json '+quote(out/('resources_hard.json' if soft_comparisons else 'resources.json')),
        'write_verilog -noattr '+quote(out/'resources.v'),
    ]
    packing=None
    if pack_dsp_registers:
        from dsp_output_register_pack import pack,prove_template
        coarse=run(commands+['blackbox =A:whitebox','write_json '+quote(out/'coarse_source.json')],out,'infer_coarse')
        packed,packing=pack(json.loads((out/'coarse_source.json').read_text()),allow_inverted_enable=pack_dsp_inverted_enable)
        packing['transition_proof']=prove_template(out/'pack_proof')
        if any(r.get('enable_polarity',1)==0 for r in packing['packed']):
            packing['inverted_enable_proof']=prove_template(out/'pack_inverted_enable_proof',enable_polarity=0)
        packing.update(status='proved',implementation_sha256=sha(ROOT/'scripts/dsp_output_register_pack.py'),
            input_sha256=sha(out/'coarse_source.json'),input_rtlil_sha256=sha(out/'inferred.il'))
        (out/'coarse_packed.json').write_text(json.dumps(packed)+'\n');packing['packed_sha256']=sha(out/'coarse_packed.json')
        # JSON does not retain RTLIL's global automatic-name counter. A new
        # process can otherwise allocate an already existing $auto$ff name.
        with (out/'inferred.il').open() as stream:
            header=[stream.readline() for _ in range(16)]
        counter=next((int(line.split()[1]) for line in header if line.startswith('autoidx ')),None)
        if counter is None:raise ValueError('Coarse RTLIL has no automatic-name counter')
        (out/'restore_autoidx.il').write_text('autoidx '+str(counter)+'\n')
        packing['autoidx_restore_sha256']=sha(out/'restore_autoidx.il')
        reload=['read_json '+quote(out/'coarse_packed.json'),'read_verilog -overwrite -lib +/gowin/cells_sim.v',
            'read_verilog -overwrite -lib +/gowin/cells_xtra_gw5a.v',
            'read_verilog -overwrite -lib '+quote(bridge/'vendor_cells.v'),*dsp_library,
            'read_rtlil '+quote(out/'restore_autoidx.il')]
        invocation=run(reload+tail,out,'infer');invocation['coarse_before_packing']=coarse
    else:invocation=run(commands+tail,out,'infer')
    if soft_comparisons:
        from lower_carry_only import lower
        from gowin_netlist_format import EXPECTED
        design,record=lower(json.loads((out/'resources_hard.json').read_text()))
        (out/'comparison_lowered.json').write_text(json.dumps(design)+'\n')
        record.update(input_sha256=sha(out/'resources_hard.json'),lowered_sha256=sha(out/'comparison_lowered.json'),
                      implementation_sha256=sha(ROOT/'scripts/lower_carry_only.py'),native_model_sha256=EXPECTED)
        record['optimization']=run(['read_json '+quote(out/'comparison_lowered.json'),'opt -full','techmap','opt -fast',
                                    'check -assert -noinit','write_json '+quote(out/'resources.json'),
                                    'write_verilog -noattr '+quote(out/'resources.v')],out,'comparison')
        record['output_sha256']=sha(out/'resources.json')
        (out/'comparison_lowering.json').write_text(json.dumps(record,indent=2)+'\n')
        invocation['comparison_lowering']=record
    if register_alternatives:
        from register_alternatives import add
        (out/'resources_before_choices.json').write_bytes((out/'resources.json').read_bytes())
        design,record=add(json.loads((out/'resources.json').read_text()))
        (out/'resources.json').write_text(json.dumps(design)+'\n')
        record.update(input_sha256=sha(out/'resources_before_choices.json'),output_sha256=sha(out/'resources.json'),
                      implementation_sha256=sha(ROOT/'scripts/register_alternatives.py'))
        (out/'register_choices.json').write_text(json.dumps(record,indent=2)+'\n')
        invocation['register_alternatives']=len(record['choices'])
    if packing is not None:
        allowed={(r['module'],r['instance']) for r in packing['packed']};admitted=[]
        final=json.loads((out/'resources.json').read_text())
        for mn,m in final['modules'].items():
            for n,c in m.get('cells',{}).items():
                if int(c.get('attributes',{}).get('ng_packed_oreg','0'),2):
                    if (mn,n) not in allowed:raise ValueError('Unproved registered DSP marker')
                    admitted.append(dict(module=mn,instance=n,cell=c))
        packing['admitted_cells']=admitted;packing['resource_sha256']=sha(out/'resources.json')
        (out/'dsp_output_packing.json').write_text(json.dumps(packing,indent=2)+'\n')
        invocation['dsp_output_packing']=dict(dsps=len(admitted),removed_flipflops=packing.get('removed_flipflops',0))
    return invocation


def materialize(source, out, top, vendor_library=None, max_logic_cells=None, net_aliases=None, optimize_resources=True, map_registers=False, register_alternatives=False,logic_replication=False,register_replication=False,dedicated_cluster_replication=False,register_control_penalty_ps=0):
    out.mkdir(parents=True, exist_ok=False)
    design=json.loads(source.read_text())
    authorized_packed=set()
    if any(int(c.get('attributes',{}).get('ng_packed_oreg','0'),2) for m in design['modules'].values() for c in m.get('cells',{}).values()):
        from dsp_output_register_pack import validate
        folder=source.parent.parent/'03_resources'
        authorized_packed=validate(design,json.loads((folder/'dsp_output_packing.json').read_text()),folder)
    if register_alternatives:
        from register_alternatives import select
        record=json.loads((source.parent.parent/'03_resources/register_choices.json').read_text())
        design,selection=select(design,record,control_penalty_ps=register_control_penalty_ps)
        (out/'register_selection.json').write_text(json.dumps(selection,indent=2)+'\n')
    design,pads=insert(design,top)
    pads['constraint_net_aliases']=bind_net_aliases(design,net_aliases or {})
    pads['legalized_flipflop_aliases']=legalize_gw5a_aliases(design)
    if optimize_resources:
        from gw5a_dsp import explicit_configuration
        pads['dsp_explicit_configuration']=explicit_configuration(design,authorized_packed)
    pads['completed_unused_outputs']=complete_output_ports(design)
    if map_registers:
        from register_nextstate_map import fold
        design,register_mapping=fold(design)
        (out/'register_mapping.json').write_text(json.dumps(register_mapping,indent=2)+'\n')
        pads['register_nextstate_rewrites']=register_mapping['rewrites']
    if optimize_resources:
        from optimize_native_json import optimize
        design,optimization=optimize(design)
        (out/'optimizations.json').write_text(json.dumps(optimization,indent=2)+'\n')
    if logic_replication:
        from native_logic_replication import replicate,prove
        before=design
        design,replication=replicate(design)
        replication['proof']=prove(before,design,out/'replication_proof')
        replication['implementation_sha256']=sha(ROOT/'scripts/native_logic_replication.py')
        (out/'logic_replication.json').write_text(json.dumps(replication,indent=2)+'\n')
    replication_before=None;native_replication={}
    if register_replication or dedicated_cluster_replication:
        # The reference is independently read_json'd too. Name its dangling
        # bits before saving, as for the final graph: NEW_ID can collide with
        # identifiers retained from previous Yosys reads on large designs.
        name_unaliased_bits(design)
        replication_before=design
        (out/'pre_replication.json').write_text(json.dumps(replication_before)+'\n')
    if dedicated_cluster_replication:
        from native_cluster_replication import replicate as replicate_clusters
        from native_boundary_cec import prove as prove_boundary
        previous=design;design,cluster_record=replicate_clusters(design)
        cluster_record['proof']=prove_boundary(previous,design,out/'cluster_replication_proof')
        native_replication['clusters']=cluster_record
    if register_replication:
        from native_register_replication import replicate as replicate_registers,validate_graph,prove_sdk
        previous=design;design,state_record=replicate_registers(design)
        state_record['graph_proof']=validate_graph(previous,design,state_record)
        state_record['state_proof']=prove_sdk(out/'register_replication_state_proof')
        native_replication['registers']=state_record
    pads['named_unaliased_bits']=name_unaliased_bits(design)
    from gw5a_functional_map import validate_packing
    pads['dedicated_mux_packing']=validate_packing(design)
    resources=count(design,top)
    (out/'resource_preflight.json').write_text(json.dumps(resources,indent=2)+'\n')
    if max_logic_cells is not None and resources['estimated_logic_cells']>max_logic_cells:
        raise ValueError('Expanded logic exceeds the configured preflight budget; optimize before native P&R')
    (out/'with_pads.json').write_text(json.dumps(design)+'\n')
    pressure=route_fanout(design,top)
    pressure['input_sha256']=sha(out/'with_pads.json')
    (out/'route_fanout.json').write_text(json.dumps(pressure,indent=2)+'\n')
    print('Routing diagnostic:',pressure['high_fanout_nonclock_nets'],
          'non-clock nets with fanout >=',pressure['threshold'],'(physical LW allocation unmeasured)',flush=True)
    (out/'pads.json').write_text(json.dumps(pads,indent=2)+'\n')
    commands = ['read_json '+quote(out/'with_pads.json'),
        # read_json does not restore parameter declarations for black boxes.
        'read_verilog -overwrite -lib +/gowin/cells_sim.v',
        'read_verilog -overwrite -lib +/gowin/cells_xtra_gw5a.v']
    if vendor_library:
        commands.append('read_verilog -overwrite -lib '+quote(vendor_library))
    dsp_library=source.parent.parent/'03_resources/dsp_cells.v'
    if dsp_library.exists():commands.append('read_verilog -overwrite -lib '+quote(dsp_library))
    commands += [
        'blackbox =A:whitebox',
        'hierarchy -check -top '+top,
        # Resolve synthesis don't-care constants (e.g. unused BRAM data lanes)
        # before hilomap; the native mapped-netlist parser requires real nets.
        'setundef -params -zero',
        'hilomap -singleton -hicell VCC V -locell GND G',
        'clean -purge',
        'check -assert -noinit',
        # The native mapped-netlist reader accepts scalar assigns, not sliced
        # assign LHS expressions. Match Yosys's documented vendor output mode.
        'splitnets -ports',
        'write_json '+quote(out/'final.json'),
        'write_verilog -simple-lhs -decimal -noattr -defparam -renameprefix ng_ '+quote(out/'raw.vg'),
    ]
    if replication_before is not None:
        reference_commands=list(commands[:-2])
        reference_commands[0]='read_json '+quote(out/'pre_replication.json')
        reference_commands+=['write_json '+quote(out/'pre_replication_canonical.json'),'design -reset']
        commands=reference_commands+commands
    invocation=run(commands,out,'emit')
    if replication_before is not None:
        from native_boundary_cec import prove as prove_boundary
        actual=json.loads((out/'final.json').read_text())
        if register_replication:
            from native_register_replication import collapse_emitted
            actual=collapse_emitted(actual,native_replication['registers'])
        native_replication['actual_emitted_boundary_cec']=prove_boundary(
            json.loads((out/'pre_replication_canonical.json').read_text()),actual,out/'actual_replication_cec')
        native_replication.update(reference_sha256=sha(out/'pre_replication_canonical.json'),actual_sha256=sha(out/'final.json'))
        (out/'native_replication.json').write_text(json.dumps(native_replication,indent=2)+'\n')
    invocation['route_fanout_report']='route_fanout.json'
    text,record=normalize((out/'raw.vg').read_text(encoding='utf-8'))
    (out/'core.vg').write_text(text,encoding='utf-8')
    (out/'mapped.vg').write_text(text,encoding='utf-8')
    record.update(raw_sha256=sha(out/'raw.vg'),core_sha256=sha(out/'core.vg'))
    (out/'format.json').write_text(json.dumps(record,indent=2)+'\n')
    return invocation


def main():
    entry=time.perf_counter()
    ap=argparse.ArgumentParser()
    ap.add_argument('--config',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--stop-after',choices=['frontend','resources','mapping','bitstream'],default='bitstream')
    ap.add_argument('--resume',action='store_true',help='Verify a completed mapping checkpoint, then retry emission/P&R')
    ap.add_argument('--net-alias',action='append',default=[],metavar='LEGACY=MODULE:NET',help='Emission-only scalar alias binding for an existing CST object')
    ap.add_argument('--pnr-timeout',type=int,help='Native implementation watchdog in seconds (maximum 3600; timeout is failure)')
    ap.add_argument('--total-timeout',type=int,help='Shared synthesis plus P&R wall budget, at most 3600 seconds')
    args=ap.parse_args()
    if args.pnr_timeout is not None and args.pnr_timeout<=0:ap.error('P&R timeout must be positive')
    config=json.loads(args.config.read_text())
    if config.get('strategy')=='abc9_box' and config.get('register_alternatives',False):
        ap.error('abc9_box requires register_alternatives=false; choice bindings use pre-mapping wire IDs')
    if config.get('strategy')=='abc9_box' and config.get('pack_dsp_registers',False):
        ap.error('DSP packing certificates currently require the stable-wire architecture mapper')
    if config.get('auto_hierarchy',False) not in (False,True,'dsp','stateful'):
        ap.error('auto_hierarchy must be false, true, dsp or stateful')
    pnr_timeout=args.pnr_timeout if args.pnr_timeout is not None else config.get('timeout',3600)
    if not isinstance(pnr_timeout,int) or isinstance(pnr_timeout,bool) or not 0<pnr_timeout<=3600:
        ap.error('P&R timeout must be an integer from 1 to 3600 seconds; exceeding one hour is failure')
    total_timeout=args.total_timeout if args.total_timeout is not None else config.get('total_timeout',3600)
    from flow_budget import Budget,activate
    # Validate the limit before launching any native worker.
    Budget(total_timeout)
    allowed={'project','sdc','cst','rao','device_config','period_ps','strategy','physical_profile','timeout','total_timeout','macro_profile','no_iob_registers','output_iob_registers','keep_hierarchy','auto_hierarchy','max_logic_cells','net_aliases','resource_optimizations','soft_comparisons','register_nextstate_mapping','register_alternatives','architecture_recipe','logic_replication','pack_dsp_registers','pack_dsp_inverted_enable','register_replication','dedicated_cluster_replication','register_control_penalty_ps'}
    if set(config)-allowed:
        raise ValueError('Unknown flow configuration keys')
    if type(config.get('pack_dsp_inverted_enable',False)) is not bool:
        raise ValueError('pack_dsp_inverted_enable must be Boolean')
    if config.get('pack_dsp_inverted_enable') and not config.get('pack_dsp_registers'):
        raise ValueError('Inverted DSP enables require DSP register packing')
    if any(type(config.get(key,False)) is not bool for key in ('no_iob_registers','output_iob_registers')):
        raise ValueError('IOB register options must be booleans')
    if config.get('no_iob_registers') and config.get('output_iob_registers'):
        raise ValueError('Core-only and output-IOB register policies are mutually exclusive')
    aliases=dict(config.get('net_aliases',{}))
    for value in args.net_alias:
        legacy,source=value.split('=',1);module,net=source.split(':',1)
        aliases[legacy]=dict(module=module,net=net)
    def path(key):
        if not config.get(key):return None
        value=Path(config[key])
        return value.resolve() if value.is_absolute() else (ROOT/value).resolve()
    out=args.out.resolve()
    resumed=None
    prior=0
    if args.resume:
        retained=json.loads((out/'manifest.json').read_text())
        prior=sum(r['wall_seconds'] for r in retained['stages'] if r['name'] in ('frontend','bridge','resources','mapping'))
    # Checkpoint verification consumes the same budget as emission and P&R.
    # Activate before re-proving so its external solvers are clipped too.
    budget=Budget(total_timeout,prior+time.perf_counter()-entry);activate(budget)
    budget.remaining()
    if args.resume:
        if args.stop_after!='bitstream':raise ValueError('Resume currently targets final emission/P&R only')
        from check_flow_checkpoint import check
        resumed=check(out,args.config)
        aliases={**resumed[0].get('effective_net_aliases',{}),**aliases}
        history=out/'attempts';history.mkdir(exist_ok=True)
        attempt=history/('attempt_'+str(len(list(history.glob('attempt_*.json'))))+'.json')
        attempt.write_text(json.dumps(resumed[0],indent=2)+'\n')
        for name in ['05_netlist','06_pnr']:
            source=out/name
            if not source.exists():continue
            target=out/(name+'_failed_'+str(len(list(out.glob(name+'_failed_*')))))
            if not source.resolve().is_relative_to(out) or not target.resolve().is_relative_to(out):
                raise ValueError('Attempt archive path escapes flow directory')
            source.rename(target)
    else:out.mkdir(parents=True,exist_ok=False)
    result=dict(schema='nextgowin-cpu-flow-v1',status='running',execution='single-threaded CPU synthesis/mapping',
        config=config,config_sha256=sha(args.config),stages=[] if resumed is None else [r for r in resumed[0]['stages'] if r['name'] in ('frontend','bridge','resources','mapping')],board_programmed=False,
        resumed_verified_mapping=resumed is not None,
        effective_net_aliases=aliases,
        effective_pnr_timeout=pnr_timeout,
        frontend_only_vendor=True,own_mapping_source='Resource-inferred Boolean graph; not vendor-mapped user LUTs')
    def save():(out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    def stage(name,action):
        budget.remaining()
        start=time.perf_counter();result['active_stage']=name;save()
        print('Starting',name,flush=True)
        value=action()
        result['stages'].append(dict(name=name,wall_seconds=time.perf_counter()-start,result=value));save()
        budget.remaining()
        print('Completed',name,flush=True)
        return value
    start=entry
    result['total_timeout_seconds']=total_timeout
    try:
        frontend=resumed[1] if resumed else stage('frontend',lambda:export(path('project'),out/'01_frontend'))
        top=frontend['top']
        if not resumed and args.stop_after!='frontend':
            def bridge():
                folder=out/'02_bridge';folder.mkdir()
                exported=(out/'01_frontend/elaborated.v').read_text(encoding='utf-8')
                macro=None
                if path('macro_profile'):
                    from gowin_macros import prepare
                    exported,payload,macro=prepare(exported,path('macro_profile'),path('rao'))
                    (folder/'macro.vg').write_text(payload,encoding='utf-8')
                    (folder/'macro.json').write_text(json.dumps(macro,indent=2)+'\n')
                text,record=convert(exported)
                (folder/'design.v').write_text(text,encoding='utf-8')
                (folder/'vendor_cells.v').write_text(record.pop('vendor_library_verilog'),encoding='utf-8')
                record.update(source_sha256=sha(out/'01_frontend/elaborated.v'),
                    output_sha256=sha(folder/'design.v'),vendor_library_sha256=sha(folder/'vendor_cells.v'))
                (folder/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
                return dict(initial_values=len(record['initial_values']),memories=len(record['restored_memories']),
                            primitive_specializations=len(record['primitive_specializations']))
            stage('bridge',bridge)
            stage('resources',lambda:infer(out/'02_bridge',out/'03_resources',top,config.get('keep_hierarchy',[]),config.get('resource_optimizations',True),config.get('soft_comparisons',False),config.get('register_alternatives',False),config.get('auto_hierarchy',False),config.get('pack_dsp_registers',False),config.get('pack_dsp_inverted_enable',False)))
        if args.stop_after not in ('frontend','resources'):
            mapped=resumed[2] if resumed else stage('mapping',lambda:map_design(out/'03_resources/resources.json',out/'04_mapping',
                config['period_ps'],config.get('strategy','acd_remap'),config.get('architecture_recipe','wide')))
            result['local_model_budgets_satisfied']=mapped['all_local_budgets_satisfied']
            def emit():
                value=materialize(out/'04_mapping/mapped.json',out/'05_netlist',top,out/'02_bridge/vendor_cells.v',config.get('max_logic_cells'),aliases,config.get('resource_optimizations',True),config.get('register_nextstate_mapping',False),config.get('register_alternatives',False),config.get('logic_replication',False),config.get('register_replication',False),config.get('dedicated_cluster_replication',False),config.get('register_control_penalty_ps',0))
                if (out/'02_bridge/macro.vg').exists():
                    with (out/'05_netlist/mapped.vg').open('a',encoding='utf-8') as stream:
                        stream.write('\n'+(out/'02_bridge/macro.vg').read_text(encoding='utf-8'))
                    value['linked_native_macro_sha256']=sha(out/'02_bridge/macro.vg')
                value['final_netlist_sha256']=sha(out/'05_netlist/mapped.vg')
                return value
            stage('emit',emit)
        if args.stop_after=='bitstream':
            def physical():
                effective_timeout=int(budget.timeout(pnr_timeout,reserve=5))
                if effective_timeout<1:raise TimeoutError('Insufficient total flow budget to launch native P&R')
                result['effective_pnr_timeout']=effective_timeout;save()
                command=[sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(out/'05_netlist/mapped.vg'),
                    '--sdc',str(path('sdc')),'--out',str(out/'06_pnr'),'--bitstream',
                    '--timeout',str(effective_timeout)]
                for key,flag in [('cst','--cst'),('rao','--rao'),('device_config','--device-config')]:
                    if path(key):command.extend([flag,str(path(key))])
                if config.get('no_iob_registers'):command.append('--no-iob-registers')
                elif config.get('output_iob_registers'):command.append('--output-iob-registers')
                with (out/'physical_runner.log').open('w') as log:
                    subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
                record=json.loads((out/'06_pnr/manifest.json').read_text())
                if path('physical_profile'):
                    from project_timing import audit
                    checked=audit(out/'06_pnr',json.loads(path('physical_profile').read_text()))
                else:
                    checked=summaries((out/'06_pnr/mapped.tr').read_text())
                (out/'06_pnr/physical_summary.json').write_text(json.dumps(checked,indent=2)+'\n')
                return dict(native=record,timing=checked)
            physical=stage('pnr_bitstream',physical)
            result['timing_closed']=physical['timing']['timing_closed']
        result.update(status='complete',stop_after=args.stop_after,wall_seconds=time.perf_counter()-start,
                      total_stage_wall_seconds=sum(r['wall_seconds'] for r in result['stages']),total_budget_used_seconds=budget.used())
        save();print(json.dumps({k:v for k,v in result.items() if k!='stages'},indent=2))
    except Exception as error:
        result.update(status='failed',error=str(error),wall_seconds=time.perf_counter()-start,total_budget_used_seconds=budget.used());save();raise


if __name__=='__main__':main()
