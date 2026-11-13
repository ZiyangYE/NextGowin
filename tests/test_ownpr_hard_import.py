import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_native_netlist import emit,inactive_clock_projection
from ownpr_hard_architecture import HardArchitecture
from audit_ownpr_native_export import connectivity_check
from ownpr_image_storage import sha


@pytest.fixture(scope='module')
def state():
    folder=ROOT/'results/vexriscv_full_placement_import_005'
    read=lambda n:json.loads((folder/n).read_text())
    meta=read('manifest.json')
    assert meta['status']=='placement_imported' and not meta['vendor_pnr_executed']
    assert sha(folder/'native_connectivity.json')==meta['connectivity_sha256']
    assert sha(folder/'binding_report.json')==meta['binding_report_sha256']
    assert sha(folder/'hard_import_readback.json')==meta['hard_readback_sha256']
    return dict(design=json.loads((ROOT/'results/vexriscv_own_hard_place_007/packed.json').read_text()),
        mapping=read('name_map.json'),actual=read('native_connectivity.json'),bindings=read('binding_report.json'))


def test_all_hard_bus_bits_and_existing_soft_cells_read_back_exactly(state):
    result=connectivity_check(**state)
    assert result['pins']==27460 and result['hard_instances']==14
    assert state['bindings']['hard_bindings_restored']==14
    text,mapping=emit(state['design'],hard=HardArchitecture())
    assert mapping==state['mapping']
    assert text==(ROOT/'results/vexriscv_full_placement_import_005/probe.vg').read_text()
    assert len(mapping['hard_inactive_clock_projection'])==8


def test_native_modes_and_pin_coordinates_match_independent_calibration(state):
    from ownpr_hard_timing_model import profile
    model=HardArchitecture();cells=state['design']['modules']['top']['cells']
    result=json.loads((ROOT/'results/vexriscv_full_placement_import_005/hard_import_readback.json').read_text())
    assert result['status']=='hard_mode_and_endpoint_readback_matched' and result['hard_cells']==14
    assert not result['initialization_bits_verified'] and not result['timing_signoff']
    for row in result['records']:
        assert row['mode_fields']==model.mode_attributes[profile(cells[row['instance']])]
        assert len(row['mode_fields'])==(248 if row['primitive']=='MULTALU27X18' else 93)


def test_dsp_attribute_extent_matches_pinned_constructor_and_excludes_pointers():
    import struct,hashlib
    from native_hard_binding import DSP_ATTRIBUTE_COUNT
    from ownpr_device import DLL_SHA
    file=ROOT/'runtime_bundle/gowin/IDE/bin/gowin.dll';data=file.read_bytes()
    assert hashlib.sha256(data).hexdigest()==DLL_SHA
    pe=struct.unpack_from('<I',data,0x3c)[0];sections=struct.unpack_from('<H',data,pe+6)[0];optional=struct.unpack_from('<H',data,pe+20)[0]
    def read_rva(rva,count):
        for i in range(sections):
            off=pe+24+optional+i*40;size,start,raw_size,raw=struct.unpack_from('<4I',data,off+8)
            if start<=rva and rva+count<=start+raw_size:return data[raw+rva-start:raw+rva-start+count]
        raise AssertionError('RVA outside section')
    # mov ecx,0xf8; rep stosw, following lea rdi,[rcx+0x22].
    assert read_rva(0x1faf2c,8)==bytes.fromhex('b9f800000066f3ab')
    assert DSP_ATTRIBUTE_COUNT==248 and 0x22+DSP_ATTRIBUTE_COUNT*2<=0x218


def test_single_wrong_bus_bit_is_rejected(state):
    changed=copy.deepcopy(state)
    pin=next(r for r in changed['actual'] if r['pin']=='DOUT[0]')
    pin['net']='incorrect_bus_order'
    with pytest.raises(ValueError,match='connectivity'):connectivity_check(**changed)


def test_projection_cannot_be_extended_to_a_registered_dsp_clock(state):
    design=copy.deepcopy(state['design']);contracts=HardArchitecture().contract(design)
    name=next(n for n,r in contracts.items() if r['primitive']=='MULTALU27X18')
    design['modules']['top']['cells'][name]['parameters']['OREG_CLK']='CLK0'
    with pytest.raises(ValueError,match='Registered DSP'):inactive_clock_projection(design,contracts)


def test_unapproved_hard_serializer_and_missing_binding_fail(state):
    with pytest.raises(ValueError,match='Unsupported native handoff primitive'):emit(state['design'])
    changed=copy.deepcopy(state);changed['bindings']['bindings']=[r for r in changed['bindings']['bindings'] if r['kind']!='HARD']
    with pytest.raises(ValueError,match='hard-resource binding'):connectivity_check(**changed)
