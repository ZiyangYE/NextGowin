"""Cache a synchronous internal state function using an existing next-state cone.

Original state is preserved. An exhaustive next-function check plus folded
boundary CEC proves the added state invariant and the original transition
relation, including feedback through the cached control. Full STA is required.
"""
import argparse,copy,json,time,shutil
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from ownpr_multilevel_cut import Cuts
from ownpr_output_state_cache import plan
from ownpr_existing_state_alias import admissible
from native_boundary_cec import functions,prove
from workspace_budget import reserve


def synchronous_fanout(design,bit,clock):
    cells=design['modules']['top']['cells'];model=Cuts(design);users={}
    for n,c in cells.items():
        for p,bs in c['connections'].items():
            if c['port_directions'][p]!='output':
                for b in bs:users.setdefault(b,[]).append((n,p))
    pending=[bit];seen=set();boundaries=set()
    while pending:
        b=pending.pop()
        if b in seen:continue
        seen.add(b)
        for n,p in users.get(b,[]):
            c=cells[n];typ=c['type']
            if 'CLK' in p.upper() or typ=='GSR':raise ValueError('Cache reaches a clock/global-reset input')
            if typ.startswith('DFF'):
                if not admissible(c) or c['connections']['CLK']!=[clock]:raise ValueError('Unmodeled asynchronous/CDC state consumer')
                boundaries.add(n);continue
            if typ=='OBUF':boundaries.add(n);continue
            if functions(c):
                pending.extend(v for port,bs in c['connections'].items() if c['port_directions'][port]=='output' for v in bs);continue
            # Hard blocks can bypass internal registers. Until their mode and
            # downstream clock domains are modeled, they are not boundaries.
            raise ValueError('Unsupported internal-cache consumer '+n+'/'+p)
    return sorted(boundaries)


def transform(design,layout,root,next_driver):
    if int(design['modules']['top'].get('attributes',{}).get('blackbox','0'),2):raise ValueError('Opaque top module is not supported')
    cells=design['modules']['top']['cells'];model=Cuts(design)
    if root not in model.functions or next_driver not in layout['clusters']:raise ValueError('Missing function or next-state LUT')
    bit=model.functions[root][1];slot=layout['clusters'][next_driver]
    if cells[next_driver]['type'] not in ('LUT1','LUT2','LUT3','LUT4') or slot.get('ff') or slot.get('wide_ff_root') or slot['slot'] not in (0,2,4):raise ValueError('Next-state LUT lacks a free admitted FF position')
    allbits=[b for c in cells.values() for bs in c['connections'].values() for b in bs]+[b for p in design['modules']['top']['ports'].values() for b in p['bits']]+[b for p in design['modules']['top'].get('netnames',{}).values() for b in p['bits']]
    fresh=max(b for b in allbits if type(b)==int)+1;probe=copy.deepcopy(design);probe_name='__nextgowin_state_function_probe'
    if probe_name in cells:raise ValueError('Probe name collision')
    probe['modules']['top']['cells'][probe_name]=dict(type='OBUF',attributes={},parameters={},port_directions={'I':'input','O':'output'},connections={'I':[bit],'O':[fresh]})
    info=plan(probe,probe_name,True);next_bit=cells[next_driver]['connections']['F'][0];word=int(info['truth_hex'],16)
    init_address=sum(r['initial']<<r['index'] for r in info['states'])
    if (word>>init_address)&1:raise ValueError('Cache requires INIT=0')
    if len(info['variables'])>18:raise ValueError('Next-state proof exceeds bounded scope')
    boundaries=synchronous_fanout(design,bit,info['clock']);constant_values={b:int(v) for b,v in model.constants.items()}
    for a in range(1<<len(info['variables'])):
        values={**constant_values,**{b:(a>>i)&1 for i,b in enumerate(info['variables'])}};address=0;active=set()
        for r in info['states']:
            value=0 if values[r['reset']] else values[r['d']] if values[r['ce']] else values[r['q']];address|=value<<r['index']
        def evaluate(b):
            if b in values:return values[b]
            n=model.drivers.get(b)
            if n not in model.functions or n in active:raise ValueError('Existing next term is outside the state boundary')
            active.add(n);ins,out,truth=model.functions[n]
            if out!=b:raise ValueError('Function output binding mismatch')
            v=(truth>>sum(evaluate(x)<<i for i,x in enumerate(ins)))&1;active.remove(n);values[b]=v;return v
        if evaluate(next_bit)!=((word>>address)&1):raise ValueError('Existing next-state term is not equivalent')
    new=copy.deepcopy(design);placed=copy.deepcopy(layout);nc=new['modules']['top']['cells'];name='ng_internal_state_0';i=0
    while name in nc:i+=1;name='ng_internal_state_'+str(i)
    sinks=[]
    for n,c in cells.items():
        for p,bs in c['connections'].items():
            if c['port_directions'][p]!='input' or bit not in bs:continue
            if c['type'] not in ('LUT1','LUT2','LUT3','LUT4','OBUF'):raise ValueError('Direct consumer needs a separate binding-aware rewrite')
            nc[n]['connections'][p]=[fresh if v==bit else v for v in bs];sinks.append((n,p))
    if not sinks:raise ValueError('No observed function loads')
    constants={v:next((b for b,x in model.constants.items() if x==v and type(b)==int),None) for v in ('0','1')}
    if None in constants.values():raise ValueError('Explicit GND/VCC bindings required')
    nc[name]=dict(type='DFFRE',attributes={},parameters={'INIT':'0'},port_directions={'D':'input','Q':'output','CLK':'input','CE':'input','RESET':'input'},connections={'D':[next_bit],'Q':[fresh],'CLK':[info['clock']],'CE':[constants['1']],'RESET':[constants['0']]})
    placed['clusters'][next_driver]['ff']=name;placed.update(timing_signoff=False,physical_legality_complete=False)
    folded=copy.deepcopy(new);del folded['modules']['top']['cells'][name]
    for c in folded['modules']['top']['cells'].values():c['connections']={p:[bit if v==fresh else v for v in bs] for p,bs in c['connections'].items()}
    reference=copy.deepcopy(design);observation='__nextgowin_cached_next_value'
    if observation in reference['modules']['top']['ports']:raise ValueError('Observation name collision')
    for d in (reference,folded):d['modules']['top']['ports'][observation]=dict(direction='output',bits=[next_bit])
    return new,placed,reference,folded,dict(root=root,root_bit=bit,next_driver=next_driver,next_bit=next_bit,new_ff=name,new_q=fresh,rewired_sinks=sinks,source_states=info['states'],truth_hex=info['truth_hex'],initial_value=0,step_vectors=1<<len(info['variables']),synchronous_boundaries=boundaries,lut_delta=0,register_delta=1,original_state_preserved=True)


def verify_materialized(before,after,layout,placed,change):
    """Reconstruct and compare the actual graph, including the added FF.

    Folding establishes the old transition relation only under the invariant;
    exact reconstruction additionally checks INIT and every new FF connection.
    """
    expected,expected_layout,reference,folded,certificate=transform(before,layout,change['root'],change['next_driver'])
    if after!=expected or placed!=expected_layout or json.loads(json.dumps(change))!=json.loads(json.dumps(certificate)):
        raise ValueError('Materialized cache differs from the proven construction')
    if reference!=folded:raise ValueError('Folded transition graph differs')
    from ownpr_ff_configuration import compile_initial
    compile_initial(after,placed)
    return dict(materialized_graph_exact=True,register_init_and_connections_checked=True,
                folded_transition_graph_identical=True,physical_control_groups_checked=True,
                step_vectors=certificate['step_vectors'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('base','out','cst','sdc'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--root',required=True);p.add_argument('--next-driver',required=True);a=p.parse_args();start=time.monotonic();reserve(ROOT,30_000_000)
    old=load_json(a.base/'packed.json');layout=load_json(a.base/'placement.json');meta=load_json(a.base/'manifest.json')
    if meta['status']!='placed' or meta['netlist_sha256']!=sha(a.base/'packed.json') or meta['placement_sha256']!=sha(a.base/'placement.json'):raise ValueError('Changed base')
    from ownpr_place import constraints
    contract=constraints(old['modules']['top'],a.cst.read_text(),a.sdc.read_text())
    new,placed,reference,folded,change=transform(old,layout,a.root,a.next_driver)
    clock=new['modules']['top']['cells'][change['new_ff']]['connections']['CLK']
    if not any(c['type']=='IBUF' and c['connections']['O']==clock and c['connections']['I']==old['modules']['top']['ports'][contract['clock_port']]['bits'] for c in old['modules']['top']['cells'].values()):raise ValueError('Cache clock is not the constrained primary clock')
    from ownpr_critical_chain_move import apply as validate
    validate(new,placed,Device(),dict(sites={}))
    verification=verify_materialized(old,new,layout,placed,change)
    a.out.mkdir(exist_ok=False);proof=prove(reference,folded,a.out/'folded_cec',120)
    if reference!=folded and not proof or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Incomplete folded-state proof')
    for n,v in [('packed.json',new),('placement.json',placed),('change.json',change)]:(a.out/n).write_text(json.dumps(v,indent=2))
    shutil.copyfile(__file__,a.out/'builder_source.py.txt')
    result=dict(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),source_netlist_sha256=sha(a.base/'packed.json'),source_placement_sha256=sha(a.base/'placement.json'),change=change,folded_graphs_structurally_identical=reference==folded,folded_transition_and_observation_cec=proof,proof_scope='Joint synchronous invariant: new_Q=F(old_Q); original transitions and outputs plus the reused next-state term are unchanged under folding. No waveform/CDC claim.',clock_replan_required=True,interface_cycles_changed=False,timing_signoff=False,wall_seconds=time.monotonic()-start)
    result['materialized_verification']=verification
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(change,indent=2))


if __name__=='__main__':main()
