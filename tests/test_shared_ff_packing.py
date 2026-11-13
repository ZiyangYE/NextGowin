import copy,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_shared_ff_packing import calibration,compile_sites
from ownpr_ff_configuration import compile_initial
from ownpr_pack_shared_ff import propose


def fixture(shared=True,dynamic=False):
    cells={}
    for i in range(2):
        cells[f'l{i}']=dict(type='LUT1',parameters={'INIT':'10'},port_directions={'I0':'input','F':'output'},connections={'I0':[20 if i else 2],'F':[10+i]})
        cells[f'f{i}']=dict(type='DFFRE',parameters={},port_directions={p:'output' if p=='Q' else 'input' for p in ('D','Q','CLK','CE','RESET')},connections={'D':[10+i],'Q':[20+i],'CLK':[3],'CE':[4 if dynamic else '1'],'RESET':['0']})
    design={'modules':{'top':{'ports':{},'cells':cells}}}
    layout=dict(clusters={f'l{i}':dict(lut=f'l{i}',ff=f'f{i}',row=20 if shared or i else 10,column=20 if shared or i else 10,slot=i if shared else 0) for i in range(2)},fixed_io={},arithmetic_clusters={},mux_clusters={})
    if shared:layout['shared_ff_calibration']=calibration()['admission_sha256']
    device=SimpleNamespace(rows=40,cols=40,grid=[[17]*40 for _ in range(40)],fse=SimpleNamespace(sha256=calibration()['fse_sha256']))
    return design,layout,device


def test_calibrated_pair_combines_attributes_without_merging_state():
    d,p,device=fixture(dynamic=True);before=copy.deepcopy(d)
    groups,info=compile_sites(d,p,device)
    assert len(info['records'])==1 and info['records'][0]['ffs']==['f0','f1']
    assert groups[20,20][0][5:7]==[0,3] and d==before
    compiled=compile_initial(d,p)
    assert len(compiled['paired_data_connections'])==2 and compiled['shared_control_pairs']['full_routing_sta_required']
    assert not compiled['full_bitstream_verified']


@pytest.mark.parametrize('pin,value',[('CLK',7),('CE',8),('RESET','1')])
def test_equal_signal_classes_do_not_allow_different_control_nets(pin,value):
    d,p,device=fixture(dynamic=True);d['modules']['top']['cells']['f1']['connections'][pin]=[value]
    with pytest.raises(ValueError,match='incompatible'):compile_sites(d,p,device)


def test_admission_initialization_and_physical_capacity_are_checked():
    d,p,device=fixture();p.pop('shared_ff_calibration')
    with pytest.raises(ValueError,match='admission'):compile_sites(d,p,device)
    d,p,device=fixture();d['modules']['top']['cells']['f1']['parameters']['INIT']='1'
    with pytest.raises(ValueError,match='incompatible'):compile_sites(d,p,device)
    d,p,device=fixture();p['clusters']['l1']['slot']=0
    with pytest.raises(ValueError,match='duplicate LUT'):compile_sites(d,p,device)
    d,p,device=fixture();p['arithmetic_clusters']['a']=dict(row=20,column=20,slot=4)
    with pytest.raises(ValueError,match='uncalibrated macro'):compile_sites(d,p,device)
    d,p,device=fixture();p['clusters']['l0']['slot']=6;p['clusters']['l1']['slot']=7
    with pytest.raises(ValueError,match='slots'):compile_sites(d,p,device)


def test_joint_pair_movement_keeps_the_logic_and_reduces_all_incident_cost():
    d,p,device=fixture(shared=False);before=copy.deepcopy(d)
    result,plan=propose(d,p,device,{('l1','I0'):1.},count=1,radius=24)
    assert plan['shared_pairs']==1 and plan['moves'][0]['ff']=='f0'
    assert result['clusters']['l0']['slot']==1 and result['clusters']['l1']==p['clusters']['l1']
    assert plan['weighted_span_after']<plan['weighted_span_before'] and d==before
    assert p['clusters']['l0']['slot']==0
    d,p,device=fixture(shared=False,dynamic=True);d['modules']['top']['cells']['f1']['connections']['CE']=[9]
    with pytest.raises(ValueError,match='No compatible'):propose(d,p,device,{},count=1)


def test_global_relayout_keeps_shared_group_atomic_and_legalizes_both_slots():
    from ownpr_global_relayout import units,legalize
    from ownpr_device import Device
    d,p,_=fixture(dynamic=True);p['hard_clusters']={};device=Device()
    for row in p['clusters'].values():row.update(row=49,column=89)
    rows,owner=units(d,p);key=owner['f0']
    assert owner['f1']==owner['l0']==owner['l1']==key
    assert rows[key]['kind']=='shared' and rows[key]['area']==pytest.approx(1/3)
    result,metrics=legalize(d,p,rows,{key:(50,90)},[],device)
    assert result['clusters']['l0']['row']==result['clusters']['l1']['row']
    assert result['clusters']['l1']['slot']==result['clusters']['l0']['slot']+1
    assert compile_initial(d,result)['shared_control_pairs']['records'][0]['ffs']==['f0','f1']


def test_incidental_protected_lut_cannot_be_swapped_out_for_a_shared_pair():
    d,p,device=fixture(shared=False)
    d['modules']['top']['cells']['protected']=copy.deepcopy(d['modules']['top']['cells']['l0'])
    d['modules']['top']['cells']['protected']['connections']['F']=[30]
    p['clusters']['protected']=dict(lut='protected',ff=None,row=20,column=20,slot=1)
    result,plan=propose(d,p,device,{('l1','I0'):1.},count=1,radius=24,protected={'protected'})
    assert result['clusters']['protected']==p['clusters']['protected']
    assert all(m['displaced_lut']!='protected' for m in plan['moves'])


def test_native_shared_pair_audit_rejects_a_mismatched_actual_q_node():
    from ownpr_image_storage import load_json
    from ownpr_device import Device
    from audit_shared_ff_route import check
    folder=ROOT/'results/vexriscv_shared_ff_001';d=load_json(folder/'candidate/packed.json');p=load_json(folder/'candidate/placement.json')
    captures=load_json(folder/'implementation/export/native/sta_endpoint_audit.json');bindings=load_json(folder/'implementation/export/native/binding_report.json');device=Device()
    result=check(d,p,device,captures,bindings)
    assert result['pairs']==64 and result['registers']==128
    left,right=result['records'][0]['ffs'];a=next(r for r in captures if (r['instance'],r['pin'])==(left,'Q'));b=next(r for r in captures if (r['instance'],r['pin'])==(right,'Q'))
    b['nodes'][0]['node']=a['nodes'][0]['node']
    with pytest.raises(ValueError,match='actual pin'):check(d,p,device,captures,bindings)
