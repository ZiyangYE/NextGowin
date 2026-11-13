import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json
from ownpr_device import Device
from ownpr_macro_replication import replicate
from ownpr_regional_replication import replicate as region


def source():
    base=ROOT/'results/vexriscv_pin_batch_reproduction_001/candidate/placement'
    return load_json(base/'packed.json'),load_json(base/'placement.json'),Device()


def test_wide_copy_can_feed_alu_data_without_modifying_carry_topology():
    d,p,dev=source();cells=d['modules']['top']['cells'];drivers={b:n for n,c in cells.items() for pin,bs in c['connections'].items() if c['port_directions'][pin]=='output' for b in bs}
    fanout={}
    for c in cells.values():
        for pin,bits in c['connections'].items():
            if c['port_directions'][pin]=='input':
                for b in bits:fanout[b]=fanout.get(b,0)+1
    sink,pin,root=next((n,pin,drivers[cells[n]['connections'][pin][0]]) for n in p['arithmetic_clusters'] for pin in ('I0','I1','I3') if cells[drivers[cells[n]['connections'][pin][0]]]['type']=='MUX2_LUT6' and fanout[cells[n]['connections'][pin][0]]>1)
    new,layout,change=replicate(d,p,dev,root,[(sink,pin)])
    assert layout['arithmetic_clusters']==p['arithmetic_clusters']
    assert layout['fixed_io']==p['fixed_io']
    old=cells[sink];updated=new['modules']['top']['cells'][sink]
    restored=copy.deepcopy(updated);restored['connections'][pin]=old['connections'][pin]
    assert restored==old and updated['connections'][pin]==[change['new_bit']]
    assert change['lut_growth']==4 and change['mux_growth']==3
    for name in p['arithmetic_clusters']:
        for port in ('CIN','COUT','SUM'):
            assert cells[name]['connections'][port]==new['modules']['top']['cells'][name]['connections'][port]
    with pytest.raises(ValueError,match='input'):
        replicate(d,p,dev,root,[(sink,'CIN')])


def test_state_frontier_region_can_terminate_at_alu_and_preserves_original_states():
    d,p,dev=source();cells=d['modules']['top']['cells']
    # This is a retained timing witness, not a name-based optimization policy.
    root='ng_coopt_1460';bit=cells[root]['connections']['F'][0]
    sinks=[(n,pin) for n in p['arithmetic_clusters'] for pin in ('I0','I1','I3') if cells[n]['connections'][pin]==[bit]]
    assert sinks
    new,layout,change=region(d,p,dev,root,sinks,4)
    assert change['register_replicas']
    for name,c in cells.items():
        if c['type'].startswith('DFF'):assert new['modules']['top']['cells'][name]==c
    assert layout['arithmetic_clusters']==p['arithmetic_clusters']
    assert layout['fixed_io']==p['fixed_io']
    from ownpr_register_equivalence import collapse
    _,_,_,invariants=collapse(d,new,change['register_replicas'])
    assert len(invariants)==len(change['register_replicas'])
    with pytest.raises(ValueError,match='input'):
        region(d,p,dev,root,[(sinks[0][0],'CIN')],4)


def test_wide_replica_can_clear_local_capacity_and_reports_moved_clocks():
    d,p,dev=source();cells=d['modules']['top']['cells'];root='ng_coopt_1074';bit=cells[root]['connections']['O'][0]
    sinks=[(n,pin) for n in p['arithmetic_clusters'] for pin in ('I0','I1','I3') if cells[n]['connections'][pin]==[bit]]
    new,layout,change=replicate(d,p,dev,root,sinks,legalize=True)
    audit=change['legalization'];assert audit and change['lut_growth']==4
    assert change['clocks_changed']==audit['clock_replan_required']
    assert layout['fixed_io']==p['fixed_io'] and layout['arithmetic_clusters']==p['arithmetic_clusters']
    # Only legalization witnesses may move original ordinary clusters.
    moved={m['lut'] for m in audit['moves']}
    for n,site in p['clusters'].items():
        if n not in moved:assert layout['clusters'][n]==site
    assert len(layout['clusters'])==len(p['clusters'])+4
    assert new['modules']['top']['ports']==d['modules']['top']['ports']


def test_selected_launch_frontier_does_not_duplicate_other_state():
    d,p,dev=source();cells=d['modules']['top']['cells'];root='ng_coopt_1460';bit=cells[root]['connections']['F'][0]
    sinks=[(n,pin) for n in p['arithmetic_clusters'] for pin in ('I0','I1','I3') if cells[n]['connections'][pin]==[bit]]
    _,_,all_state=region(d,p,dev,root,sinks,4)
    parents=set(all_state['register_replicas'].values());assert len(parents)>1
    chosen=sorted(parents)[0]
    new,layout,change=region(d,p,dev,root,sinks,4,allowed_registers={chosen})
    assert set(change['register_replicas'].values())=={chosen}
    assert change['register_growth']==1
    old_states={n for n,c in cells.items() if c['type'].startswith('DFF')}
    new_states={n for n,c in new['modules']['top']['cells'].items() if c['type'].startswith('DFF')}
    assert new_states-old_states==set(change['register_replicas'])
    for n in old_states:assert new['modules']['top']['cells'][n]==cells[n]
    with pytest.raises(ValueError,match='state frontier'):
        region(d,p,dev,root,sinks,4,allowed_registers={root})


def test_small_state_and_logic_cone_is_packed_in_one_cfu():
    d,p,dev=source();cells=d['modules']['top']['cells'];root='ng_coopt_169';bit=cells[root]['connections']['F'][0]
    sinks=[(n,pin) for n,c in cells.items() for pin,bs in c['connections'].items() if n=='ng_coopt_948' and c['port_directions'][pin]=='input' and bs==[bit]]
    assert sinks
    selected=next(n for n,c in cells.items() if c['type'] in ('DFFRE','DFFSE') and c['connections']['Q']==cells[root]['connections']['I0'])
    new,layout,change=region(d,p,dev,root,sinks,4,allowed_registers={selected},cluster_region=True)
    assert change['register_growth']==1 and change['lut_growth']==2
    assert len({tuple(row['site'][:2]) for row in change['placements']})==1
    assert tuple(change['cluster_site'])==tuple(change['placements'][0]['site'][:2])
    assert layout['arithmetic_clusters']==p['arithmetic_clusters'] and layout['fixed_io']==p['fixed_io']
    from ownpr_register_equivalence import collapse
    collapse(d,new,change['register_replicas'])
