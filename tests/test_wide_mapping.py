import copy,itertools,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_wide_mapping import implement
from ownpr_multilevel_cut import Cuts
from ownpr_mux_macros import validate
from native_boundary_cec import functions


class Device:
    rows=4;cols=4;grid=[[17]*4 for _ in range(4)]


def fixture(width,paired=False):
    def lut(bits,out):
        word=sum((a.bit_count()%2)<<a for a in range(1<<len(bits)))
        return dict(type='LUT'+str(len(bits)),parameters={'INIT':format(word,'0'+str(1<<len(bits))+'b')},attributes={},hide_name=1,connections={**{'I'+str(i):[b] for i,b in enumerate(bits)},'F':[out]},port_directions={**{'I'+str(i):'input' for i in range(len(bits))},'F':'output'})
    leftwidth=3 if width==5 else 4
    cells={'left':lut(list(range(1,leftwidth+1)),100),'root':lut([100]+list(range(leftwidth+1,width+1)),101),'gnd':dict(type='GND',parameters={},attributes={},connections={'G':[0]},port_directions={'G':'output'})}
    if paired:cells['ff']=dict(type='DFFRE',parameters={'INIT':'0'},attributes={},connections=dict(D=[101],Q=[102],CLK=[8],CE=[9],RESET=[0]),port_directions=dict(D='input',Q='output',CLK='input',CE='input',RESET='input'))
    d=dict(modules=dict(top=dict(cells=cells,ports={'inputs':dict(direction='input',bits=list(range(1,width+1))), 'reserved':dict(direction='input',bits=[999]),'output':dict(direction='output',bits=[101])},netnames={})))
    l=dict(clusters={'left':dict(lut='left',ff=None,row=0,column=0,slot=0),'root':dict(lut='root',ff='ff' if paired else None,row=0,column=1,slot=0)},mux_clusters={},wide_macros=[],arithmetic_clusters={},hard_clusters={},fixed_io={})
    cut=Cuts(d,k=7).function('root',list(range(1,width+1)))
    return d,l,cut


def output(d,assignment):
    bits=dict(assignment);pending=[f for c in d['modules']['top']['cells'].values() for f in functions(c)]
    while pending:
        ready=[]
        for i,(ins,out,truth) in enumerate(pending):
            if all(b in bits for b in ins):bits[out]=(truth>>sum(bits[b]<<j for j,b in enumerate(ins)))&1;ready.append(i)
        assert ready
        pending=[f for i,f in enumerate(pending) if i not in set(ready)]
    return bits[101]


@pytest.mark.parametrize('width',[5,6,7])
@pytest.mark.parametrize('buffered',[False,True])
def test_wide_mapping_preserves_truth_and_native_reversed_child_slots(width,buffered):
    d,l,cut=fixture(width);before=copy.deepcopy(d);order=list(reversed(cut['cut_bits']))
    nd,nl,c=implement(d,l,cut,(2,2),order,'test',buffered)
    check=validate(nd,nl,Device());assert check['capacity_verified'] and d==before
    assert len(check['links'])==2*((1<<(width-4))-1)
    leaves=sorted((n,r['slot']) for n,r in nl['clusters'].items() if n.startswith('ng_wide_') and '_leaf_' in n)
    assert leaves[0][1]=={5:0,6:2,7:6}[width]
    assert nd['modules']['top']['cells']['root']['hide_name']==1
    for values in itertools.product((0,1),repeat=width):
        assignment=dict(zip(range(1,width+1),values));assert output(d,assignment)==output(nd,assignment)


def test_paired_root_requires_buffer_and_keeps_register_data_and_site():
    d,l,cut=fixture(6,True)
    with pytest.raises(ValueError,match='paired'):implement(d,l,cut,(2,2),cut['cut_bits'],'x',False)
    nd,nl,c=implement(d,l,cut,(2,2),cut['cut_bits'],'x',True)
    assert nd['modules']['top']['cells']['ff']==d['modules']['top']['cells']['ff'] and nl['clusters']['root']==l['clusters']['root']
    assert nd['modules']['top']['cells']['root']['connections']['F']==[101] and nd['modules']['top']['cells'][c['wide_root']]['connections']['O'][0]>999


def test_occupied_cfu_and_forged_cut_fail_closed():
    d,l,cut=fixture(6)
    with pytest.raises(ValueError,match='vacant'):implement(d,l,cut,(0,0),cut['cut_bits'],'x')
    cut['truth_hex']='0x1'
    with pytest.raises(ValueError,match='certificate'):implement(d,l,cut,(2,2),cut['cut_bits'],'x')


def test_cofactor_specific_pruning_and_fast_pin_reassignment_preserve_mixed_support():
    d,l,_=fixture(6);cells=d['modules']['top']['cells'];left=cells['left'];left.update(type='LUT3',parameters={'INIT':'10000000'},connections=dict(I0=[1],I1=[2],I2=[3],F=[100]),port_directions=dict(I0='input',I1='input',I2='input',F='output'))
    cells['right']=dict(type='LUT2',parameters={'INIT':'0110'},attributes={},connections=dict(I0=[4],I1=[5],F=[104]),port_directions=dict(I0='input',I1='input',F='output'))
    cells['root'].update(type='LUT3',parameters={'INIT':'11001010'},connections=dict(I0=[100],I1=[104],I2=[6],F=[101]),port_directions=dict(I0='input',I1='input',I2='input',F='output'))
    l['clusters']['right']=dict(lut='right',ff=None,row=1,column=0,slot=0);cut=Cuts(d,k=7).function('root',list(range(1,7)))
    nd,nl,change=implement(d,l,cut,(2,2),cut['cut_bits'],'pruned')
    leaves=[nd['modules']['top']['cells'][n] for n in change['neighbor_luts']]
    assert any(sum(c['connections']['I'+str(i)]!=[0] for i in range(4))<4 for c in leaves)
    for values in itertools.product((0,1),repeat=6):
        bits=dict(zip(range(1,7),values));assert output(d,bits)==output(nd,bits)
