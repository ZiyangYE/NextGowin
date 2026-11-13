import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_hard_architecture import HardArchitecture,assignment,unused_pad_projection
from ownpr_device import Device,RoutingGraph
from ownpr_route_design import bind
from native_boundary_cec import functions


def read(path):return json.loads(path.read_text())


def implementation():
    p=ROOT/'results/vexriscv_own_hard_place_007'
    return read(p/'packed.json'),read(p/'placement.json'),read(p/'manifest.json')


def test_full_cpu_capacity_and_selected_bus_endpoints():
    design,placed,meta=implementation();model=HardArchitecture();device=Device()
    result=model.validate(design,placed,device)
    assert result['families']=={'bsram':10,'new_dsp':4} and len(placed['fixed_io'])==284
    audit=model.selected_bindings(design,placed,ROOT/'results/vexriscv_hard_selected_bindings_002')
    graph=RoutingGraph(device,ROOT/'results/native_rrg_001');nets,static=bind(design,placed,graph,hard_bindings=audit)
    assert len(nets)==6024 and len(next(n for n in nets if n.get('clock'))['sinks'])==1546
    assert audit['constant_pins_omitted']==28 and not audit['configuration_verified']
    with pytest.raises(ValueError,match='audited'):bind(design,placed,graph)


def test_exclusive_assignment_and_collision_rejection():
    assert assignment([[1.,2.],[1.,100.]])==[1,0]
    with pytest.raises(ValueError):assignment([[1.],[2.]])
    design,placed,_=implementation();bad=copy.deepcopy(placed)
    names=[n for n,x in bad['hard_clusters'].items() if x['primitive']=='DPX9B']
    bad['hard_clusters'][names[1]]=copy.deepcopy(bad['hard_clusters'][names[0]])
    with pytest.raises(ValueError,match='collision'):HardArchitecture().validate(design,bad,Device())


def test_dynamic_head_keeps_carry_function_and_inactive_pins_fail_closed():
    design,_,meta=implementation();change=meta['packing']['dynamic_carry_heads']
    assert len(change)==1
    cell=design['modules']['top']['cells'][change[0]['seed']];_,_,truth=functions(cell)[1]
    assert [(truth>>(1|(b<<1)|4))&1 for b in (0,1)]==[0,1]
    bad=copy.deepcopy(design);dsp=next(c for c in bad['modules']['top']['cells'].values() if c['type']=='MULTALU27X18')
    dsp['connections']['CASI'][0]=dsp['connections']['A'][0]
    with pytest.raises(ValueError,match='inactive hard input'):HardArchitecture().contract(bad)


def test_added_unused_pads_cannot_hide_observable_logic():
    before=read(ROOT/'results/vexriscv_global_logic_001/comparators/netlist.json');after,_,meta=implementation();pads=meta['packing']['materialized_unused_inputs']
    assert len(pads)==2 and meta['packing']['unused_input_buffers_proven_unobservable']
    projection=unused_pad_projection(before,after,pads)
    assert all(p['cell'] not in projection['modules']['top']['cells'] for p in pads)
    bad=copy.deepcopy(after);out=bad['modules']['top']['cells'][pads[0]['cell']]['connections']['O'][0]
    next(c for c in bad['modules']['top']['cells'].values() if c['type']=='LUT1')['connections']['I0']=[out]
    with pytest.raises(ValueError,match='observable consumer'):unused_pad_projection(before,bad,pads)
