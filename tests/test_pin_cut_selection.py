import copy,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_pin_cut_selection import discover,select_roots,cone_paths
from ownpr_multilevel_cut import Cuts,replace
from native_boundary_cec import prove


def fixture():
    def lut(ins,out,truth):return dict(type='LUT2',parameters={'INIT':format(truth,'04b')},connections=dict(I0=[ins[0]],I1=[ins[1]],F=[out]),port_directions=dict(I0='input',I1='input',F='output'))
    cells={'prefix':lut([2,3],10,8),'a':lut([10,4],11,14),'b':lut([10,5],12,6)}
    d={'modules':{'top':dict(cells=cells,ports=dict(inputs=dict(direction='input',bits=[2,3,4,5]),outputs=dict(direction='output',bits=[11,12])),netnames={})}}
    p=dict(clusters={n:dict(lut=n,ff=None,row=i,column=0,slot=0) for i,n in enumerate(cells)},fixed_io={})
    budgets={(n,'F'):dict(original_setup_slack_ns=s) for n,s in [('prefix',.005),('a',.01),('b',.02)]}
    wire={(n,p):1. for n,c in cells.items() for p,v in c['port_directions'].items() if v=='input'}
    return d,p,budgets,wire,SimpleNamespace(grid=[[17]*3 for _ in range(3)])


def test_unreported_roots_and_shared_preserved_prefix_are_optimized_independently(tmp_path):
    d,p,b,w,device=fixture();before=copy.deepcopy(d)
    options,info=discover(d,p,b,w,device,reported_roots={'a'})
    assert 'b' in info['additional_roots_not_in_reported_paths'] and any(r['cut']['root']=='b' for r in options)
    chosen,coverage=select_roots(options,count=2)
    assert {r['cut']['root'] for r in chosen}=={'a','b'} and coverage['shared_preserved_interiors_allowed']
    after=d
    for r in chosen:after,_=replace(after,r['cut'],r['late_bit'])
    assert after['modules']['top']['cells']['prefix']==before['modules']['top']['cells']['prefix'] and d==before
    assert all(r['cec']['equivalent'] for r in prove(before,after,tmp_path/'proof',30))


def test_source_wire_is_not_counted_as_eliminated_internal_delay():
    d,p,b,w,dev=fixture();model=Cuts(d);cut=model.function('a',{2,3,4})
    a=cone_paths(model,cut,w,lambda n,p:.2);w['prefix','I0']=100.;z=cone_paths(model,cut,w,lambda n,p:.2)
    assert z[2][0]>a[2][0] and abs((z[2][0]-z[2][1])-(a[2][0]-a[2][1]))<1e-10


def test_root_dependencies_still_conflict_and_clock_logic_is_excluded():
    def row(root,inside,gain):return dict(cut=dict(root=root,interior=inside),lut_growth=1,worst_slack_ns=.0,priority_gain_proxy_ns=gain)
    chosen,_=select_roots([row('a',['a','shared'],2.),row('b',['a','b'],1.)],count=2)
    assert [r['cut']['root'] for r in chosen]==['a']
    d,p,b,w,dev=fixture();d['modules']['top']['cells']['ff']=dict(type='DFFRE',connections=dict(CLK=[10],D=[2],Q=[40],CE=['1'],RESET=['0']),port_directions={n:'output' if n=='Q' else 'input' for n in ('CLK','D','Q','CE','RESET')})
    options,_=discover(d,p,b,w,dev);assert options==[]


def test_partial_macro_does_not_capture_another_pending_root():
    from ownpr_macro_legalize import clear_site
    from test_ownpr_macro_legalize import sample
    d,p,dev=sample();p['clusters']['anchor'].update(row=1,column=1,slot=6)
    cost=lambda r,c:100*(abs(r-1)+abs(c-1))
    _,site,_=clear_site(d,p,'root',dev,cost,wide_leaf_slots=(0,1),reserved_roots={'anchor'})
    assert site!=(1,1)
