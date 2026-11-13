import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_lut_graph_feedback import FixedBoundaryLutFeedback


def lut(bits,out,word):
    return dict(type='LUT'+str(len(bits)),parameters={'INIT':word},attributes={},port_directions={**{f'I{i}':'input' for i in range(len(bits))},'F':'output'},connections={**{f'I{i}':[b] for i,b in enumerate(bits)},'F':[out]})


class Library:
    def lut_input(self,i,slot,selector):return [.05,.05,.5+slot*.01,.5+slot*.01]


def fixture():
    cells={'in':dict(type='IBUF',connections={'O':[1]},port_directions={'O':'output'}),'a':lut([1],2,'10'),'root':lut([2],3,'10'),'out':dict(type='OBUF',connections={'I':[3],'O':[4]},port_directions={'I':'input','O':'output'})}
    d=dict(modules=dict(top=dict(cells=cells,ports={},netnames={})))
    site=lambda n,s:dict(lut=n,ff=None,row=1,column=1,slot=s)
    layout=dict(clusters={'a':site('a',0),'root':site('root',1)},fixed_io={})
    def e(s,d,kind,delay):return dict(source=s,destination=d,kind=kind,delay=[delay]*2,transitions=[(0,0),(1,1)])
    g=dict(nodes=['in/O','a/I0','a/F','root/I0','root/F','out/I','out/O'],seeds={'in/O':[1.,1.]},edges=[e('in/O','a/I0','wire',.2),e('a/I0','a/F','cell',.5),e('a/F','root/I0','wire',.3),e('root/I0','root/F','cell',.51),e('root/F','out/I','wire',.4),e('out/I','out/O','io',.1)])
    model=dict(setup_requirements={'out/O':[3.,3.]},hold_requirements={'out/O':[.1,.1]})
    return d,layout,g,model


def test_changed_topology_drops_old_lut_pin_arcs_and_rebuilds_measured_siblings():
    d,layout,g,model=fixture();before=copy.deepcopy(g);f=FixedBoundaryLutFeedback(model,{'setup':g,'hold':g},d,layout,[],Library())
    new=copy.deepcopy(d);new['modules']['top']['cells']['root']=lut([1],3,'10')
    logical=[dict(instance=n,pin=p,net=net,node=node) for n,p,net,node in [('a','I0','net_1',11),('root','I0','net_1',12),('out','I','net_3',13)]]
    m={'net_1':{11:[.6,.6,.1,.1],12:[.2,.2,.1,.1]},'net_3':{13:[.4,.4,.1,.1]}}
    graphs=f.rebuild(new,layout,logical,m,[])
    assert any(e['source']=='in/O' and e['destination']=='root/I0' for e in graphs['setup']['edges'])
    assert not any(e['source']=='a/F' and e['destination']=='root/I0' for e in graphs['setup']['edges'])
    assert next(e for e in graphs['setup']['edges'] if e['destination']=='a/I0')['delay']==[.6,.6]
    assert g==before and f.metrics(graphs)['hold']['violations']==0
    del m['net_1'][11]
    with pytest.raises(ValueError,match='complete measured tree'):f.rebuild(new,layout,logical,m,[])


def test_fixed_boundary_and_dedicated_link_changes_are_rejected():
    d,l,g,m=fixture();f=FixedBoundaryLutFeedback(m,{'setup':g,'hold':g},d,l,[],Library())
    bad=copy.deepcopy(d);bad['modules']['top']['cells']['out']['connections']['I']=[2]
    with pytest.raises(ValueError,match='fixed state'):f.admit(bad,l,[])
    with pytest.raises(ValueError,match='dedicated/static'):f.admit(d,l,[dict(kind='new_link')])
    moved=copy.deepcopy(l);moved['fixed_io']['new']={}
    with pytest.raises(ValueError,match='placement'):f.admit(d,moved,[])


def test_paired_data_uses_audited_direct_link_not_loaded_output_stub():
    d,l,g,m=fixture();d['modules']['top']['cells']['r']=dict(type='DFFRE',parameters={},connections={'D':[3],'Q':[5],'CLK':[6]},port_directions={'D':'input','Q':'output','CLK':'input'})
    l['clusters']['root']['ff']='r';g['nodes'].append('r/D');g['edges'].append(dict(source='root/F',destination='r/D',kind='wire',delay=[0.,0.],transitions=[(0,0),(1,1)]))
    f=FixedBoundaryLutFeedback(m,{'setup':g,'hold':g},d,l,[dict(cell='r',pin='CE')],Library())
    logical=[dict(instance=n,pin=p,net=net,node=node) for n,p,net,node in [('a','I0','net_1',11),('root','I0','net_2',12),('out','I','net_3',13),('r','D','net_3',14),('r','CLK','clock',15),('r','CLK','clock',16)]]
    measured={'net_1':{11:[.2]*4},'net_2':{12:[.3]*4},'net_3':{13:[.4]*4,14:[.009]*4}}
    graphs=f.rebuild(d,l,logical,measured,[dict(cell='r',pin='CE')])
    assert next(e for e in graphs['setup']['edges'] if e['destination']=='r/D')['delay']==[0.,0.]
    bad=copy.deepcopy(l);bad['clusters']['root']['slot']=2
    with pytest.raises(ValueError,match='paired register'):f.admit(d,bad,[dict(cell='r',pin='CE')])


def test_five_input_network_preserves_truth_after_selector_reordering():
    from ownpr_multilevel_cut import Cuts
    from ownpr_joint_lut_search import implement
    from register_control_map import logic
    parity=lambda n:format(sum((a.bit_count()%2)<<a for a in range(1<<n)),f'0{1<<n}b')
    d=dict(modules=dict(top=dict(cells={'a':lut([1,2,3],6,parity(3)),'root':lut([6,4,5],7,parity(3))},ports={},netnames={})))
    d['modules']['top']['cells']['gnd']=dict(type='GND',connections={'G':[8]},port_directions={'G':'output'},parameters={},attributes={})
    d['modules']['top']['cells']['root']['hide_name']=0
    l=dict(clusters={'a':dict(lut='a',ff=None,row=0,column=0,slot=0),'root':dict(lut='root',ff=None,row=0,column=0,slot=1)})
    cut=Cuts(d,k=5).function('root',[1,2,3,4,5]);new,nl,change=implement(d,l,cut,2,[(0,0,1),(0,0,2),(0,0,3)],'test')
    functions={out:(ins,word) for c in new['modules']['top']['cells'].values() if logic(c) for ins,out,word in [logic(c)]}
    for assignment in range(32):
        values={i+1:assignment>>i&1 for i in range(5)}
        def value(bit):
            if bit in ('0','1'):return int(bit)
            if bit==8:return 0
            if bit not in values:
                ins,word=functions[bit];values[bit]=word>>sum(value(b)<<i for i,b in enumerate(ins))&1
            return values[bit]
        assert value(7)==assignment.bit_count()%2
    assert len(change['added_luts'])==2 and nl['clusters']['root']['slot']==1
    assert new['modules']['top']['cells']['root']['connections']['I3']==[2]
    assert new['modules']['top']['cells']['root']['hide_name']==0


def test_same_net_luts_still_cannot_share_one_slot():
    d,l,g,m=fixture();f=FixedBoundaryLutFeedback(m,{'setup':g,'hold':g},d,l,[],Library());bad=copy.deepcopy(l)
    bad['clusters']['root']['slot']=bad['clusters']['a']['slot']
    with pytest.raises(ValueError,match='Overlapping'):f.admit(d,bad,[])
