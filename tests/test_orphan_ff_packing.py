"""Mixed odd-only groups require exact native evidence and preserved semantics."""
import copy,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_orphan_ff_calibration import calibration as orphan_calibration
from ownpr_shared_ff_packing import calibration,compile_sites
from ownpr_ff_configuration import single_ff_attributes


def fixture(base=0,dynamic=False):
    cells={};clusters={};other=(base+2)%6
    for i,slot in enumerate((base+1,other)):
        cells[f'l{i}']={'type':'LUT1','parameters':{'INIT':'10'},'connections':{'I0':[2+i],'F':[10+i]},'port_directions':{'I0':'input','F':'output'}}
        cells[f'f{i}']={'type':'DFFRE','parameters':{},'connections':{'D':[10+i],'Q':[20+i],'CLK':[3],'CE':[5+i if dynamic or i else '1'],'RESET':['0']},'port_directions':{p:'output' if p=='Q' else 'input' for p in ('D','Q','CLK','CE','RESET')}}
        clusters[f'l{i}']={'lut':f'l{i}','ff':f'f{i}','row':20,'column':20,'slot':slot}
    cells['unrelated']=copy.deepcopy(cells['l0']);cells['unrelated']['connections']['F']=[30]
    clusters['unrelated']={'lut':'unrelated','ff':None,'row':20,'column':20,'slot':6}
    d={'modules':{'top':{'ports':{},'cells':cells}}};p={'clusters':clusters,'arithmetic_clusters':{},'mux_clusters':{},
        'shared_ff_calibration':calibration()['admission_sha256'],'orphan_ff_calibration':orphan_calibration()}
    dev=SimpleNamespace(rows=40,cols=40,grid=[[17]*40 for _ in range(40)],fse=SimpleNamespace(sha256=calibration()['fse_sha256']))
    return d,p,dev


@pytest.mark.parametrize('base',(0,2,4))
@pytest.mark.parametrize('dynamic',(False,True))
def test_odd_ff_stays_in_place_with_other_live_groups(base,dynamic):
    d,p,dev=fixture(base,dynamic);before=copy.deepcopy((d,p));groups,info=compile_sites(d,p,dev)
    assert (d,p)==before and len(info['orphan_groups'])==1 and not info['records']
    assert groups[20,20][base//2]==single_ff_attributes('DFFRE',base+1,'signal' if dynamic else 'one','zero')[base//2]
    assert info['full_routing_sta_required']


@pytest.mark.parametrize('mutation',('token','init','reset','zero_ce','wide','wrong_data','wrong_type','macro','slot7'))
def test_orphan_admission_rejects_unverified_semantics_or_geometry(mutation):
    d,p,dev=fixture();f=d['modules']['top']['cells']['f0']
    if mutation=='token':p.pop('orphan_ff_calibration')
    elif mutation=='init':f['parameters']['INIT']='1'
    elif mutation=='reset':f['connections']['RESET']=[9]
    elif mutation=='zero_ce':f['connections']['CE']=['0']
    elif mutation=='wide':p['clusters']['l0']['independent_ff_input']=True
    elif mutation=='wrong_data':f['connections']['D']=[99]
    elif mutation=='wrong_type':f['type']='DFFCE'
    elif mutation=='macro':p['arithmetic_clusters']['a']={'row':20,'column':20,'slot':4}
    elif mutation=='slot7':p['clusters']['l0']['slot']=7
    with pytest.raises(ValueError):compile_sites(d,p,dev)


def test_actual_native_orphan_readback_rejects_wrong_q_slot():
    from ownpr_device import Device,ROOT
    from ownpr_image_storage import load_json
    from audit_shared_ff_route import check
    root=ROOT/'results/dsp_orphan_routing_trial_025';placed=root/'placement_002';native=root/'implementation_001/export/native'
    d=load_json(placed/'packed.json');p=load_json(placed/'placement.json')
    captures=load_json(native/'sta_endpoint_audit.json');bindings=load_json(native/'binding_report.json');device=Device()
    result=check(d,p,device,captures,bindings)
    assert result['orphan_registers']==16 and result['all_verified_registers']==452
    first=result['orphan_records'][0];q=next(row for row in captures if (row['instance'],row['pin'])==(first['ff'],'Q'))
    q['nodes'][0]['node'][-1]=device.ids['Q0']
    with pytest.raises(ValueError,match='Orphan FF actual pin'):check(d,p,device,captures,bindings)
