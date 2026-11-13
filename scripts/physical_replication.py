"""Bounded, timing-report-driven same-cut LUT replication in a flat core.

Measured slow wires nominate source/sink pairs. A fitted placement-snapshot
heuristic ranks hypothetical copies near their sinks; P&R must validate them.
No placement coordinate is imposed, and no estimated timing closure is claimed.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
from audit_project_remap import check_statements
from gowin_cones import parse,atom,readback
from wire_feedback import samples,predict
from run_abc_reproduction import ROOT,cec,sha


def resolve_name(native,prefix,by_name,mapping):
    if prefix:
        if not native.startswith(prefix+'/'):return None
        native=native[len(prefix)+1:]
    return native if native in by_name else mapping.get(native)


def verify_copies(original,replacement,actions):
    old={n['statement'].name:n for n in original['luts']};new={n['statement'].name:n for n in replacement['luts']}
    if set(new)!=set(old)|{a['clone_name'] for a in actions}:raise ValueError('Unexpected LUT instance set')
    aliases={a['clone_output']:old[a['source_name']]['output'] for a in actions}
    for name,node in old.items():
        actual=new[name]
        if (actual['truth']!=node['truth'] or actual['output']!=node['output'] or
            [aliases.get(n,n) for n in actual['inputs']]!=node['inputs']):
            raise ValueError('LUT graph differs after collapsing same-cut copies')
    for action in actions:
        node,copy=old[action['source_name']],new[action['clone_name']]
        if copy['truth']!=node['truth'] or copy['inputs']!=node['inputs'] or copy['output']!=action['clone_output']:
            raise ValueError('Copy is not the original truth table and cut')
    check_statements(original,replacement)
    return True


def replicate(text,root,report,model,prefix='',mapping=None,max_copies=8,min_wire_ns=2,min_gain_ns=1.5,max_slack_ns=.5,area_allowance=.001):
    parsed=parse(text,root);by_name={s.name:s for s in parsed['statements'] if s.pins is not None}
    nodes={n['statement'].name:n for n in parsed['luts']};mapping=mapping or {}
    observed=samples(report);ranked=[];rejected=Counter()
    for row in observed:
        source=resolve_name(row['source'],prefix,by_name,mapping);sink=resolve_name(row['sink'],prefix,by_name,mapping)
        if row['source_pin']!='F' or source not in nodes or sink not in nodes:
            rejected['non_lut_or_guarded_endpoint']+=1;continue
        if row['delay_ns']<min_wire_ns or row['slack_ns']>max_slack_ns or row['fanout']<2:
            rejected['wire_slack_or_fanout_threshold']+=1;continue
        src,dst=nodes[source],nodes[sink]
        # Physical pin permutations can differ from the source netlist. Bind by
        # the actual source wire, never by the printed physical input index.
        indices=[i for i,n in enumerate(dst['inputs']) if n==src['output']]
        if len(indices)!=1:rejected['ambiguous_or_changed_connectivity']+=1;continue
        known_inputs=set();increases=[]
        for incoming in observed:
            if resolve_name(incoming['sink'],prefix,by_name,mapping)!=source:continue
            upstream=resolve_name(incoming['source'],prefix,by_name,mapping)
            if upstream not in by_name or incoming['source_pin'] not in by_name[upstream].pins:continue
            try:net=atom(by_name[upstream].pins[incoming['source_pin']])
            except ValueError:continue
            if net not in src['inputs']:continue
            known_inputs.add(net)
            moved=dict(incoming,sink_position=row['sink_position'],fanout=incoming['fanout']+1)
            increases.append(predict(model,moved)-predict(model,incoming))
        unknown={n for n in src['inputs'] if n not in parsed['constants']}-known_inputs
        new_wire=predict(model,dict(row,source_position=row['sink_position'],fanout=1))
        penalty=max([0]+increases)+.25*len(unknown)
        gain=row['delay_ns']-new_wire-penalty
        if gain<min_gain_ns:rejected['estimated_gain_threshold']+=1;continue
        ranked.append(dict(source_name=source,sink_name=sink,logical_input_index=indices[0],
            reported_sink_pin=row['sink_pin'],observed_wire=row,estimated_local_wire_gain_ns=gain,
            known_critical_input_count=len(known_inputs),unknown_input_penalty_ns=.25*len(unknown)))
    ranked.sort(key=lambda a:(a['observed_wire']['slack_ns'],-a['estimated_local_wire_gain_ns'],a['source_name'],a['sink_name']))
    limit=min(max_copies,math.floor(len(nodes)*area_allowance));chosen=[];pins=set();added_loads=Counter()
    for row in ranked:
        pin=(row['sink_name'],row['logical_input_index'])
        src=nodes[row['source_name']]
        if pin in pins or any(added_loads[n]>=4 for n in src['inputs']):continue
        if len(chosen)>=limit:break
        chosen.append(row);pins.add(pin);added_loads.update(src['inputs'])
    if not chosen:raise ValueError('No eligible timing-driven LUT copies; preserve the seed')
    reserved=set(re.findall(r'\w+',text));tag='ngphys_'
    while any(name.startswith(tag) for name in reserved):tag='_'+tag
    edits={};insert=[];declarations=[]
    def wire(name):return name+' ' if name.startswith('\\') else name
    def statement(kind,name,pins):return kind+' '+name+'('+','.join('.'+p+'('+wire(n)+')' for p,n in pins.items())+');'
    for i,row in enumerate(chosen):
        src,dst=nodes[row['source_name']],nodes[row['sink_name']]
        name,net=tag+'copy'+str(i),tag+'net'+str(i)
        row.update(clone_name=name,clone_output=net)
        ports={f'I{j}':n for j,n in enumerate(src['inputs'])};ports['F']=net
        declarations.append(f'wire {net};')
        insert.extend([statement(src['statement'].kind,name,ports),
            f"defparam {name}.INIT={1<<len(src['inputs'])}'h{src['truth']:0{max(1,(1<<len(src['inputs']))//4)}X};"])
        changes=edits.setdefault(dst['statement'].name,dict(original=dst,ports={p:atom(w) for p,w in dst['statement'].pins.items()}))
        changes['ports']['I'+str(row['logical_input_index'])]=net
    body=parsed['body']
    for change in sorted(edits.values(),key=lambda c:c['original']['statement'].start,reverse=True):
        s=change['original']['statement'];body=body[:s.start]+statement(s.kind,s.name,change['ports'])+body[s.end:]
    # Gowin's mapped-netlist reader requires an explicit declaration before
    # the first reference, including existing LUTs redirected to a new wire.
    first_cell=min(s.start for s in parsed['statements'] if s.pins is not None)
    body=body[:first_cell]+'\n'.join(declarations)+'\n'+body[first_cell:]
    end=body.rfind('endmodule');body=body[:end]+'\n'+'\n'.join(insert)+'\n'+body[end:]
    candidate=text[:parsed['start']]+body+text[parsed['end']:]
    actual=parse(candidate,root);verify_copies(parsed,actual,chosen)
    if candidate[:actual['start']]!=text[:parsed['start']] or candidate[actual['end']:]!=text[parsed['end']:]:
        raise ValueError('Source outside the core changed')
    return candidate,parsed,actual,dict(actions=chosen,rejection_counts=dict(rejected),original_eligible_luts=len(nodes),
        copied_luts=len(chosen),area_cap=len(nodes)+limit,maximum_added_loads_per_input=4,
        parameters=dict(max_copies=max_copies,min_wire_ns=min_wire_ns,min_gain_ns=min_gain_ns,
            max_slack_ns=max_slack_ns,area_allowance=area_allowance,unknown_input_penalty_ns=.25),
        policy='Native setup slack, measured slow wires, estimated copy-at-sink gain; exact same-cut copies, no hard-cell rewiring or fixed placement',
        scope='Placement-feedback heuristic on reported paths, not complete critical-DAG coverage or a signoff timing bound')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('input',type=Path);ap.add_argument('--root',required=True)
    ap.add_argument('--report',type=Path,required=True);ap.add_argument('--wire-model',type=Path,required=True)
    ap.add_argument('--instance-prefix',default='');ap.add_argument('--leaf-mapping',type=Path)
    ap.add_argument('--max-copies',type=int,default=8);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    mapping={}
    if args.leaf_mapping:
        mapping={'/'.join(p.lstrip('\\') for p in row['original_path']):row['name'] for row in json.loads(args.leaf_mapping.read_text())}
    fitted=json.loads(args.wire_model.read_text());model=fitted['model']
    if len(model['coefficients'])!=4 or any(not math.isfinite(v) or v<0 for v in model['coefficients']):
        raise ValueError('Invalid nonnegative wire heuristic')
    report_text=args.report.read_text(errors='replace')
    header=dict(re.findall(r'^<([^>]+)>:[ \t]*(.*?)[ \t]*$',report_text,re.M))
    if [header.get('Part Number'),header.get('Setup Delay Model')]!=fitted['device_corner']:
        raise ValueError('Wire heuristic device/corner mismatch')
    original=args.input.read_bytes().decode('utf-8')
    candidate,old,new,result=replicate(original,args.root,report_text,model,
        args.instance_prefix,mapping,args.max_copies)
    source=out/'source.blif';source.write_text(old['blif']);back=out/'readback.blif';back.write_text(readback(new,old))
    proof=cec(ROOT/'build/abc/_TEST/abc.exe',source,back,out/'cec.log',180)
    if not proof['equivalent']:raise RuntimeError('Replicated global cut is not proved equivalent')
    target=out/'replicated.vg';target.write_bytes(candidate.encode('utf-8'))
    result.update(status='complete',input_sha256=sha(args.input),output_sha256=sha(target),report_sha256=sha(args.report),
        wire_model_sha256=sha(args.wire_model),source_sha256=sha(Path(__file__)),cec=proof,
        collapse_copies_restores_original_lut_graph=True,non_lut_statements_preserved=True)
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='actions'},indent=2))


if __name__=='__main__':main()
