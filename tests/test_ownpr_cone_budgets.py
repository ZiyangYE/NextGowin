import copy,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_cone_budgets import receiving_cones,physical_budgets


def fixture():
    def cell(typ,inputs,outputs):
        return dict(type=typ,connections={k:[v] for k,v in {**inputs,**outputs}.items()},
            port_directions={**dict.fromkeys(inputs,'input'),**dict.fromkeys(outputs,'output')})
    cells=dict(a=cell('LUT2',dict(I0=1,I1=2),dict(F=3)),b=cell('LUT1',dict(I0=3),dict(F=4)),
        c=cell('LUT2',dict(I0=3,I1=4),dict(F=5)),f=cell('DFFRE',dict(D=5,CE=6,CLK=7),dict(Q=8)),
        o=cell('OBUF',dict(I=4),dict(O=9)),unrelated=cell('OBUF',dict(I=10),dict(O=11)))
    captures=[dict(instance=n,native_name=n,pin=p,direction='In',nodes=[dict(node=[1,1,i+1])])
        for i,(n,p) in enumerate((n,p) for n,c in cells.items() for p,d in c['port_directions'].items() if d=='input')]
    graph=SimpleNamespace(device=SimpleNamespace(names={r['nodes'][0]['node'][2]:str(r['nodes'][0]['node'][2]) for r in captures}),node=lambda r,c,n:int(n))
    tables=dict(Setup={'f/D':.5,'f/CE':.9,'o/O':.8,'unrelated/O':.001},Hold={'f/D':.3,'f/CE':.7,'o/O':.2,'unrelated/O':.01})
    return dict(modules={'top':dict(cells=cells)}),captures,graph,tables


def test_reconvergence_covers_alternate_inputs_without_unrelated_global_minimum():
    d,c,g,t=fixture();cones=receiving_cones(d,c)
    assert cones['a','I0']==cones['a','I1']==frozenset(('f/D','o/O'))
    assert cones['c','I0']==cones['c','I1']==frozenset(('f/D',))
    budgets=physical_budgets(g,d,c,t)
    assert budgets[1]['setup']==pytest.approx(.499)
    assert budgets[1]['hold']==pytest.approx(.19)
    assert budgets[1]['endpoints']==['f/D','o/O']
    assert cones['f','CLK'] is None


@pytest.mark.parametrize('cause',['unknown','cycle','dangling','missing_endpoint','shared_unknown'])
def test_incomplete_cones_do_not_receive_optimistic_budgets(cause):
    d,c,g,t=fixture();cells=d['modules']['top']['cells']
    if cause=='unknown':cells['b']['type']='UNKNOWN'
    elif cause=='cycle':cells['b']['connections']['F']=[3]
    elif cause=='dangling':cells['b']['connections']['F']=[100]
    elif cause=='missing_endpoint':del t['Hold']['o/O']
    else:
        row=copy.deepcopy(next(r for r in c if r['instance']=='f' and r['pin']=='CLK'))
        row['nodes']=[dict(node=[1,1,1])];c.append(row)
    assert 1 not in physical_budgets(g,d,c,t)


def test_terminal_boundary_does_not_propagate_through_ff_to_its_output():
    d,c,g,t=fixture();cells=d['modules']['top']['cells'];cells['unrelated']['connections']['I']=[8]
    assert receiving_cones(d,c)['c','I0']==frozenset(('f/D',))


def test_real_tmds_internal_wide_select_has_a_complete_endpoint_cone():
    from ownpr_device import Device,RoutingGraph
    from ownpr_image_storage import load_json,read_text
    from ownpr_critical_tree_rebuild import endpoint_budgets
    sta=ROOT/'results/tmds_112_2mhz_001/export/native';d=load_json(ROOT/'results/tmds_wide_ff_hold_002/round_000/placement/packed.json')
    c=load_json(sta/'sta_endpoint_audit.json');text=read_text(sta/'probe.tr');graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001')
    old,fallback=endpoint_budgets(graph,d,c,text);new,_=endpoint_budgets(graph,d,c,text,True)
    row=next(r for r in c if r['instance']=='ng_coopt_418' and r['pin']=='S0');r,col,i=row['nodes'][0]['node'];n=graph.node(r-1,col-1,graph.device.names[i])
    assert n not in old and new[n]['scope']=='structural_downstream_endpoint_slack'
    assert new[n]['hold']>fallback['hold']
    assert new[n]['endpoints'] and new[n]['full_design_sta_required']
