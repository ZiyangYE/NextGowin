import copy,sys,itertools
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_path_cone import clone_cone,fast_inputs,prioritize


def fixture():
    def lut(bits,out,truth):
        return dict(type='LUT'+str(len(bits)),parameters={'INIT':truth},attributes={},hide_name=1,
                    connections={**{'I'+str(i):[b] for i,b in enumerate(bits)},'F':[out]},
                    port_directions={**{'I'+str(i):'input' for i in range(len(bits))},'F':'output'})
    cells={'a':lut([1,2],10,'0110'),'b':lut([10,3],11,'1000'),'root':lut([10,11],12,'1110'),'far':lut([11,2],13,'0110')}
    d=dict(modules=dict(top=dict(cells=cells,netnames={},ports={'reserved':dict(direction='input',bits=[1000])})))
    l=dict(clusters={n:dict(lut=n,ff=None,row=i+1,column=1,slot=0) for i,n in enumerate(cells)},fixed_io={})
    return d,l


def evaluate(d,inputs):
    bits=dict(inputs);todo=dict(d['modules']['top']['cells'])
    while todo:
        ready=[]
        for n,c in todo.items():
            ins=[c['connections']['I'+str(i)][0] for i in range(int(c['type'][-1]))]
            if all(b in bits for b in ins):
                address=sum(bits[b]<<i for i,b in enumerate(ins));bits[c['connections']['F'][0]]=(int(c['parameters']['INIT'],2)>>address)&1;ready.append(n)
        assert ready
        for n in ready:del todo[n]
    return bits


def test_shared_cone_preserves_diamond_root_and_far_branch_truth():
    d,l=fixture();saved=copy.deepcopy(d)
    nd,nl,change=clone_cone(d,l,['a','b','root'],[(8,8,0),(8,8,1),(8,8,2)],'test')
    old=d['modules']['top']['cells'];new=nd['modules']['top']['cells']
    assert d==saved and new['far']==old['far'] and new['a']==old['a'] and new['b']==old['b']
    assert new['root']['connections']['F']==[12] and new['root']['hide_name']==1
    assert all(new[n]['connections']['F'][0]>1000 for n in change['copies'])
    for v in itertools.product((0,1),repeat=3):
        before=evaluate(d,dict(zip((1,2,3),v)));after=evaluate(nd,dict(zip((1,2,3),v)))
        assert [before[b] for b in (10,11,12,13)]==[after[b] for b in (10,11,12,13)]


def test_paired_root_retains_slot_and_output_data_link():
    d,l=fixture();l['clusters']['root']['ff']='ff'
    with pytest.raises(ValueError,match='paired root'):clone_cone(d,l,['a','b','root'],[(8,8,0),(8,8,1),(8,8,2)],'x')
    nd,nl,_=clone_cone(d,l,['a','b','root'],[(8,8,0),(8,8,1),(3,1,0)],'x')
    assert nl['clusters']['root']==l['clusters']['root'] and nd['modules']['top']['cells']['root']['connections']['F']==[12]


def test_no_touch_and_occupied_destination_fail_closed():
    d,l=fixture();d['modules']['top']['cells']['a']['attributes']['dont_touch']='1'
    with pytest.raises(ValueError,match='Protected'):clone_cone(d,l,['a','root'],[(8,8,0),(8,8,1)],'x')
    d,l=fixture()
    with pytest.raises(ValueError,match='occupied'):clone_cone(d,l,['a','root'],[(1,1,0),(8,8,1)],'x')


def test_fast_pin_permutation_and_padding_preserve_every_truth_assignment():
    for width in range(1,5):
        for word in [0,1,(1<<(1<<width))-1,0xA53C&((1<<(1<<width))-1)]:
            cell=dict(type='LUT'+str(width),parameters={'INIT':format(word,'0'+str(1<<width)+'b')},attributes={},hide_name=1,
                      connections={**{'I'+str(i):[i+1] for i in range(width)},'F':[90]},
                      port_directions={**{'I'+str(i):'input' for i in range(width)},'F':'output'})
            for order in itertools.permutations(range(width)):
                new,mapping=fast_inputs(cell,order,0)
                assert new['hide_name']==1 and new['connections']['F']==[90]
                for assignment in range(16):
                    old=sum(((assignment>>(4-width+j))&1)<<i for j,i in enumerate(order))
                    assert (int(new['parameters']['INIT'],2)>>assignment)&1==(word>>old)&1
                assert mapping['I3']=='I'+str(order[-1])


def test_per_sink_routing_weights_preserve_binding_and_use_copied_pin_origin():
    nets=[dict(name='net_1',source=5,sinks=[10,11],criticality=.5)]
    logical=[dict(net='net_1',node=10,instance='copy',pin='I3'),dict(net='net_1',node=11,instance='relaxed',pin='I1')]
    metric={'setup':dict(wns=-2.,slack={'old/I0':[-2.,-1.9],'relaxed/I1':[1.,1.]})}
    changes=dict(input_origins={'copy':'old'},physical_to_original_inputs={'copy':{'I3':'I0'}})
    new=prioritize(nets,logical,metric,changes)
    assert new[0]['sink_criticalities']=={10:1.,11:.05} and new[0]['criticality']==1.
    assert nets==[dict(name='net_1',source=5,sinks=[10,11],criticality=.5)]
