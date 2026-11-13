import copy,sys,itertools
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_shared_dag import clone_shared


def fixture():
    def lut(bits,out,word):return dict(type='LUT'+str(len(bits)),parameters={'INIT':word},attributes={},hide_name=1,connections={**{'I'+str(i):[b] for i,b in enumerate(bits)},'F':[out]},port_directions={**{'I'+str(i):'input' for i in range(len(bits))},'F':'output'})
    cells={'a':lut([1,2],10,'0110'),'b':lut([10,3],11,'1000'),'r1':lut([10,11],12,'1110'),'r2':lut([11,2],14,'0110'),'far':lut([11,3],13,'0110'),'side':lut([12,2],15,'0110')}
    d=dict(modules=dict(top=dict(cells=cells,ports={'reserved':dict(direction='input',bits=[999])},netnames={})))
    l=dict(clusters={n:dict(lut=n,ff=None,row=i+1,column=1,slot=0) for i,n in enumerate(cells)},fixed_io={})
    sites={n:(8,8,i) for i,n in enumerate(['a','b','r1','r2'])}
    return d,l,sites


def evaluate(d,values):
    bits=dict(values);pending={n:c for n,c in d['modules']['top']['cells'].items() if c['type'].startswith('LUT')}
    while pending:
        ready=[]
        for n,c in pending.items():
            ins=[c['connections']['I'+str(i)][0] for i in range(int(c['type'][-1]))]
            if all(b in bits for b in ins):bits[c['connections']['F'][0]]=(int(c['parameters']['INIT'],2)>>sum(bits[b]<<i for i,b in enumerate(ins)))&1;ready.append(n)
        assert ready
        for n in ready:del pending[n]
    return bits


def test_diamond_shared_prefix_copied_once_for_two_original_output_roots():
    d,l,sites=fixture();saved=copy.deepcopy(d)
    nd,nl,c=clone_shared(d,l,['a','b','r1','r2'],['r1','r2'],sites,'x',{},fast=False)
    new=nd['modules']['top']['cells'];assert set(new)-set(d['modules']['top']['cells'])==set(c['internal_copies'].values()) and len(c['internal_copies'])==2
    shared=new[c['internal_copies']['b']]['connections']['F']
    assert new['r1']['connections']['I1']==new['r2']['connections']['I0']==shared and shared[0]>999
    assert new['far']==d['modules']['top']['cells']['far'] and d==saved
    for v in itertools.product((0,1),repeat=3):
        a=evaluate(d,dict(zip((1,2,3),v)));b=evaluate(nd,dict(zip((1,2,3),v)))
        assert [a[i] for i in (10,11,12,13,14,15)]==[b[i] for i in (10,11,12,13,14,15)]


def test_old_region_copy_unloads_only_ordinary_side_input_never_hard_input():
    d,l,sites=fixture();d['modules']['top']['cells']['hard']=dict(type='SDPX9B',connections={'ADB':[12]},port_directions={'ADB':'input'})
    nd,nl,c=clone_shared(d,l,['a','b','r1','r2'],['r1','r2'],sites,'x',{},unload=True,fast=False)
    new=nd['modules']['top']['cells'];legacy=next(r for r in c['legacy'] if r['root']=='r1');assert new['hard']==d['modules']['top']['cells']['hard']
    assert new['side']['connections']['I0']==new[legacy['copy']]['connections']['F'] and new['r1']['connections']['F']==[12]
    assert nl['clusters'][legacy['copy']]['row']==l['clusters']['r1']['row']
    for v in itertools.product((0,1),repeat=3):
        a=evaluate(d,dict(zip((1,2,3),v)));b=evaluate(nd,dict(zip((1,2,3),v)));assert a[12]==b[12] and a[15]==b[15]


def test_frontier_may_not_depend_on_another_root_and_paired_root_may_not_move():
    d,l,sites=fixture()
    with pytest.raises(ValueError,match='frontier'):clone_shared(d,l,['a','b','r1','r2'],['a','r1','r2'],sites,'x',{},fast=False)
    l['clusters']['r1']['ff']='fixed'
    with pytest.raises(ValueError,match='paired root'):clone_shared(d,l,['a','b','r1','r2'],['r1','r2'],sites,'x',{},fast=False)


def test_displaced_state_or_protected_logic_rejected():
    d,l,sites=fixture();l['clusters']['far']['ff']='fixed'
    with pytest.raises(ValueError,match='displacement'):clone_shared(d,l,['a','b','r1','r2'],['r1','r2'],sites,'x',{},fast=False,displacements={'far':(9,9,0)})


def test_shared_dag_fast_pin_upgrade_with_ground_preserves_all_root_and_side_outputs():
    d,l,sites=fixture();d['modules']['top']['cells']['ground']=dict(type='GND',parameters={},attributes={},connections={'G':[0]},port_directions={'G':'output'})
    metric={'setup':{'arrival':{n+'/I'+str(i):[.15*i,.2*i] for n in ['a','b','r1','r2'] for i in (0,1)}}}
    nd,nl,change=clone_shared(d,l,['a','b','r1','r2'],['r1','r2'],sites,'fast',metric,unload=False,fast=True)
    for n in [*change['internal_copies'].values(),'r1','r2']:
        assert nd['modules']['top']['cells'][n]['type']=='LUT4' and change['physical_to_original_inputs'][n]['I3']=='I1'
    for v in itertools.product((0,1),repeat=3):
        values={0:0,**dict(zip((1,2,3),v))};a=evaluate(d,values);b=evaluate(nd,values)
        assert [a[i] for i in (10,11,12,13,14,15)]==[b[i] for i in (10,11,12,13,14,15)]
