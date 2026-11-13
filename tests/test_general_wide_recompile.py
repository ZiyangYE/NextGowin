import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from test_existing_wide_remap import make_fixture,Device
from ownpr_general_wide_recompile import apply,info,GeneralWideRecompileFeedback
from ownpr_existing_wide_remap import macro_truth


def grounded(width):
    d,l,static,_,_=make_fixture(width)
    d['modules']['top']['cells']['zero']=dict(type='GND',parameters={},attributes={},port_directions={'G':'output'},connections={'G':[300]})
    f=object.__new__(GeneralWideRecompileFeedback);f.reference=d;f.layout=l;f.static=static;f.device=Device()
    return d,l,static,f


@pytest.mark.parametrize('width',[5,6])
@pytest.mark.parametrize('reduced',[False,True])
def test_full_and_reduced_leaf_support_recompile_exactly(width,reduced):
    d,l,static,f=grounded(width)
    if reduced:d['modules']['top']['cells']['leaf0']['connections']['I3']=[300]
    snapshot=copy.deepcopy(d);spec,boundary,members=info(d,l,'root');truth=macro_truth(d,'root',boundary,members)
    nd,nl,_=apply(d,l,'root',2)
    assert d==snapshot and nl==l and set(nd['modules']['top']['cells'])==set(d['modules']['top']['cells'])
    result=macro_truth(nd,'root',boundary,members)
    assert (result['cut_bits'],result['truth_hex'])==(truth['cut_bits'],truth['truth_hex'])
    assert f.admit(nd,nl,static)['capacity_verified']


def test_nonuniform_seven_variable_tree_and_unrepresentable_promotion():
    d,l,static,f=grounded(6)
    d['modules']['top']['cells']['b']['connections']['S0']=[8]
    d['modules']['top']['ports']['a']['bits'].append(8)
    spec,boundary,members=info(d,l,'root');assert len(boundary)==7
    # Original root remains a legal heterogeneous decomposition. Force a
    # different pin priority so leaf truth/input assignments are recomputed.
    nd,nl,c=apply(d,l,'root',7,boundary)
    assert c['truth_support']==boundary
    assert f.admit(nd,nl,static)['capacity_verified']
    # A dense generic seven-input function cannot fit a LUT6 canonical tree
    # merely by changing its root: branch support must actually fit LUT5.
    from ownpr_general_wide_recompile import decompose
    dense=int('de4bfa1236789abc1032547698badcfedcba9876543210fedcbaabacdeedc1234',16)
    with pytest.raises(ValueError):decompose(tuple(range(7)),dense,6,list(range(7)),0)


def test_changed_observed_function_and_geometry_are_rejected():
    d,l,static,f=grounded(6);nd,nl,_=apply(d,l,'root',2)
    nd['modules']['top']['cells']['leaf0']['parameters']['INIT']='0'*16
    with pytest.raises(ValueError):f.admit(nd,nl,static)
    d['modules']['top']['ports']['probe']=dict(direction='output',bits=[100])
    with pytest.raises(ValueError):info(d,l,'root')


def test_constant_leaf_retains_a_calibrated_timed_input():
    d,l,static,f=grounded(5);d['modules']['top']['cells']['leaf0']['parameters']['INIT']='0'*16
    nd,nl,_=apply(d,l,'root',6)
    leaf=nd['modules']['top']['cells']['leaf0']
    assert leaf['parameters']['INIT']=='0'*16
    assert sum(leaf['connections']['I'+str(i)]!=[300] for i in range(4))==1
    assert leaf['connections']['I3']!=[300]
    assert f.admit(nd,nl,static)['capacity_verified']


def test_lut7_hierarchy_is_exactly_recofactored_and_all_leaves_use_real_pin_cost():
    from ownpr_mux_architecture import children
    from ownpr_mux_macros import tree
    truth=int('b6f294c10d7a358e69e2a73bc518d04f',16);cells={}
    def cell(typ,pins,parameters=None):return dict(type=typ,parameters=parameters or {},attributes={},port_directions={p:'output' if p in ('F','O','G') else 'input' for p in pins},connections={p:[b] for p,b in pins.items()})
    def build(level,slot,selector_address=0):
        name='root' if level==7 else 'mux'+str(level)+'_'+str(slot)
        outputs=[]
        for value,(typ,child_slot) in enumerate(children(level,slot)):
            selected=selector_address|(value<<(level-5))
            if typ=='LUT4':
                child='leaf'+str(child_slot);output=100+child_slot
                cells[child]=cell(typ,{**{'I'+str(i):2+i for i in range(4)},'F':output},{'INIT':format((truth>>(16*selected))&65535,'016b')})
            else:child,output=build(level-1,child_slot,selected)
            outputs.append(output)
        output=200+level*10+slot
        cells[name]=cell('MUX2_LUT'+str(level),dict(I0=outputs[0],I1=outputs[1],S0=level+1,O=output));return name,output
    _,output=build(7,3);cells['zero']=cell('GND',dict(G=300))
    d=dict(modules={'top':dict(cells=cells,netnames={},ports={'a':dict(direction='input',bits=list(range(2,9))),'y':dict(direction='output',bits=[output])})})
    spec=tree(d,'root');l=dict(clusters={n:dict(lut=n,ff=None,row=0,column=0,slot=s) for n,s in spec['leaves'].items()},mux_clusters={n:dict(r,row=0,column=0) for n,r in spec['muxes'].items()},wide_macros=[dict(root='root',root_slot=3,row=0,column=0)],fixed_io={})
    nd,nl,_=apply(d,l,'root',2)
    assert int(macro_truth(nd,'root',range(2,9),set(spec['leaves'])|set(spec['muxes']))['truth_hex'],16)==truth
    assert nd['modules']['top']['cells']['root']['connections']['S0']==[2]
