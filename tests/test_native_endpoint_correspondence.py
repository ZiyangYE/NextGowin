import copy,struct,sys
import pytest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_native_endpoint_correspondence import audit

def fixture():
    def node(i,parent):return [struct.pack('<3h',1,1,i).hex(),parent,0]
    return dict(geometry={},logical_net_connections={'100':[1,[2]],'200':[3,[4]]},logical_pin_nodes={
        str(i):dict(owner=i,label=str(i),logical_class='ordinary',net=100 if i<3 else 200,nodes=[i*10]) for i in range(1,5)},routed_endpoint_parent_nodes={'10':node(10,0),'20':node(20,10),'30':node(30,0),'40':node(40,30)})

def test_exact_correspondence_and_wrong_root():
    s=fixture();r=audit(s);assert r['status']=='all_captured_routed_endpoints_correspond' and r['matched_physical_endpoints']==2
    s['routed_endpoint_parent_nodes']['20'][1]=30
    assert audit(s)['status']=='partial_endpoint_correspondence'

def test_cycle_missing_node_and_logical_mismatch_reject():
    for kind in ('cycle','missing','net'):
        s=fixture()
        if kind=='cycle':s['routed_endpoint_parent_nodes']['20'][1]=20
        elif kind=='missing':del s['routed_endpoint_parent_nodes']['20']
        else:s['logical_pin_nodes']['2']['net']=200
        assert audit(s)['errors']

def test_two_drivers_cannot_own_a_shared_parent_resource():
    s=fixture();s['logical_pin_nodes']['3']['nodes']=[10];s['routed_endpoint_parent_nodes']['40'][1]=10
    assert audit(s)['cross_driver_conflicts']

def test_constant_ce_needs_matching_encoded_group_and_physical_port():
    s=dict(geometry={'22':dict(name='reg',physical_header=struct.pack('<4h',50,1,1,0).hex())},
        logical_net_connections={'100':[1,[2]]},logical_pin_nodes={
            '1':dict(owner=11,label='vcc.V',logical_class='.?AVVcc@NETLIST@@',net=100,nodes=[]),
            '2':dict(owner=22,label='reg.CE',logical_class='.?AVReg@NETLIST@@',net=100,nodes=[20],inverted=False)},
        routed_endpoint_parent_nodes={'20':[struct.pack('<3h',1,1,130).hex(),0,0]})
    bindings=[dict(name='reg',physical_header=[50,1,1,0],register_ports=[dict(name=None,node=None),dict(name='CE0',node=[1,1,130])])]
    attrs=[0]*19;attrs[0]=9;attrs[5]=2
    bindings[0]['cfu_cls_attributes']=[dict(group=0,attributes=copy.deepcopy(attrs))]
    cert=dict(status='captured_cls_attributes_match_encoded_bits',mismatches=[],groups=[dict(site=[0,0,0],attributes=attrs)])
    assert audit(s,bindings)['unresolved']
    assert audit(s,bindings,cert)['static_connection_counts']=={'encoded_constant_enable_one':1}
    bad=copy.deepcopy(cert);bad['groups'][0]['attributes'][5]=0
    with pytest.raises(ValueError):audit(s,bindings,bad)
    bad=copy.deepcopy(s);bad['logical_pin_nodes']['2']['inverted']=True
    assert audit(bad,bindings,cert)['unresolved']

def test_lut_to_ff_requires_exact_companion_mode_and_truth():
    s=dict(geometry={str(i):dict(name=n,physical_header=struct.pack('<4h',kind,1,1,0).hex()) for i,n,kind in [(11,'lut',24),(22,'reg',50)]},
        logical_net_connections={'100':[1,[2]]},logical_pin_nodes={
            '1':dict(owner=11,label='lut.F',logical_class='.?AVLut4@NETLIST@@',net=100,nodes=[10]),
            '2':dict(owner=22,label='reg.D',logical_class='.?AVReg@NETLIST@@',net=100,nodes=[])},
        routed_endpoint_parent_nodes={'10':[struct.pack('<3h',1,1,24).hex(),0,0]})
    bindings=[dict(name='lut',physical_header=[24,1,1,0],physical_init_word=42),dict(name='reg',physical_header=[50,1,1,0],ff_companion_lut=dict(header=[24,1,1,0],init_word=42,input_mode=0))]
    assert audit(s,bindings)['static_connection_counts']=={'same_slot_lut_to_ff':1}
    for key,value in [('input_mode',1),('init_word',43)]:
        bad=copy.deepcopy(bindings);bad[1]['ff_companion_lut'][key]=value
        assert audit(s,bad)['unresolved']


def test_gsr_requires_certificate_and_exact_inactive_source():
    s=fixture();s['logical_net_connections']={'100':[1,[2]]}
    a=s['logical_pin_nodes']['1'];b=s['logical_pin_nodes']['2']
    a.update(label='v.V',logical_class='.?AVVcc@NETLIST@@',nodes=[])
    b.update(label='gsr.GSRI',logical_class='.?AVGsr@NETLIST@@',nodes=[],inverted=False)
    cert=dict(status='constant_inactive_gsr_and_encoded_binding_verified',driver='v.V',sink='gsr.GSRI')
    assert audit(s)['unresolved']
    assert audit(s,gsr_evidence=cert)['static_connection_counts']=={'encoded_constant_inactive_global_reset':1}
    for key,value in [('inverted',True),('label','different.GSRI')]:
        bad=copy.deepcopy(s);bad['logical_pin_nodes']['2'][key]=value
        assert audit(bad,gsr_evidence=cert)['unresolved']
    a['logical_class']='ordinary'
    assert audit(s,gsr_evidence=cert)['unresolved']


def test_output_link_requires_exact_pad_and_physical_binding():
    s=fixture();s['logical_net_connections']={'100':[1,[2]]}
    a=s['logical_pin_nodes']['1'];b=s['logical_pin_nodes']['2']
    a.update(label='reg.Q',logical_class='.?AVReg@NETLIST@@',nodes=[])
    b.update(label='pad.I',logical_class='.?AVIobuf@NETLIST@@',nodes=[])
    s['geometry']={'1':dict(physical_header=struct.pack('<4h',0,4,5,1).hex())}
    s['io']=dict(io=[dict(instance='pad',site=[0,4,5,0])])
    cert=dict(status='output_ioreg_bindings_controls_and_encoded_bits_verified',registers=[dict(instance='reg',iobuf='pad',site=[4,5,0])])
    assert audit(s,ioreg_evidence=cert)['static_connection_counts']=={'encoded_output_register_direct_link':1}
    s['io']['io'][0]['site'][1]=6
    assert audit(s,ioreg_evidence=cert)['unresolved']


def test_unverified_output_contract_rejects():
    with pytest.raises(ValueError):audit(fixture(),ioreg_evidence=dict(status='unverified'))
