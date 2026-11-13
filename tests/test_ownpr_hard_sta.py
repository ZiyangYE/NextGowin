import copy,re,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,read_text
from ownpr_timing_endpoints import contract
from ownpr_hard_sta import audit_edges,audit_pulses,audit_readback

FOLDER=ROOT/'results/vexriscv_own_sta_capture_002'
PLACE=ROOT/'results/vexriscv_own_hard_place_007'


@pytest.fixture(scope='module')
def state():return load_json(PLACE/'packed.json'),load_json(FOLDER/'name_map.json')


def test_hard_and_constant_endpoint_contract_is_independent_of_report(state):
    design,mapping=state;spec=contract(design,mapping)
    assert len(spec['synchronous'])==2750 and not spec['asynchronous'] and len(spec['clocks'])==1546
    assert mapping['instances']['ng_pad_138']+'/O' in spec['optional_constants']
    changed=copy.deepcopy(design);changed['modules']['top']['cells']['ng_ownpr_pass_79']['parameters']['INIT']='01'
    # An unproved transform must not silently retain the old constant exemption.
    assert len(contract(changed,mapping)['synchronous'])==2751


def test_all_actual_hard_matrices_modes_and_pulses_are_checked(state):
    design,mapping=state
    edges=audit_edges(FOLDER,design,mapping);pulses=audit_pulses(FOLDER,design,mapping)
    readback=audit_readback(FOLDER,design,load_json(PLACE/'placement.json'),mapping)
    assert edges['observed_edges']==8080 and edges['required_dynamic_and_output_edges']==7660
    assert edges['instances']==14 and not edges['dsp_legacy_scalar_table_used']
    assert pulses['checks']==3092 and pulses['clock_wire_rows']==6184
    assert readback['mode_fields']==1922 and not readback['initialization_payload_verified']


@pytest.mark.parametrize('mutation',['missing_arc','wrong_delay'])
def test_changed_hard_arcs_cannot_pass_audit(state,monkeypatch,mutation):
    import ownpr_hard_sta as module
    design,mapping=state;index=load_json(FOLDER/'hard_timing_index.json')
    row=next(r for r in index['instances'] if r['actual_edges']==1656);file=FOLDER/row['file'];data=load_json(file)
    if mutation=='missing_arc':data['actual_edges'].pop()
    else:data['actual_edges'][0]['matrices'][0][0]+=1.
    original=module.load_json;monkeypatch.setattr(module,'load_json',lambda p:data if Path(p)==file else original(p))
    with pytest.raises(ValueError,match='hard timing|Hard timing'):audit_edges(FOLDER,design,mapping)


def test_truncated_pulse_details_are_rejected(state,monkeypatch):
    import ownpr_hard_sta as module
    design,mapping=state;file=FOLDER/'probe.tr';text=read_text(file)
    matches=list(re.finditer(r'^\s*MPW\d+\s*$',text,re.M));last=matches[-1].start();end=text.index('\n3.5 ',last)
    altered=text[:last]+text[end:]
    monkeypatch.setattr(module,'read_artifact_text',lambda p:altered if p==file else read_text(p))
    with pytest.raises(ValueError,match='pulse coverage'):audit_pulses(FOLDER,design,mapping)


def test_missing_dynamic_bram_endpoint_is_rejected(state,monkeypatch):
    import audit_ownpr_extended_sta as module
    from audit_ownpr_extended_sta import audit
    design,mapping=state;file=FOLDER/'probe.tr';text=read_text(file)
    section=re.search(r'^3\.1\.1 Setup Paths Table\n(.*?)(?=\n3\.)',text,re.M|re.S)
    row=next(line for line in section[1].splitlines() if re.search(r'own_i_\d+/\w+\[\d+\]\s+clk:',line))
    altered=text.replace(row+'\n','',1)
    monkeypatch.setattr(module,'read_artifact_text',lambda p:altered if p==file else read_text(p))
    with pytest.raises(ValueError,match='endpoint coverage'):audit(FOLDER,PLACE)


def test_hard_placement_feedback_handles_bus_bits_and_excludes_clocks():
    from ownpr_placement_feedback import load
    priorities,info=load(ROOT/'results/vexriscv_verified_export_001/native',load_json(PLACE/'packed.json'))
    assert info['paths']==2168 and len(priorities)==3849
    assert any(pin in ('ADA','ADB','DIA','DIB','DI') for name,pin in priorities)
    assert not any(pin in ('CLK','CLKA','CLKB','CIN') for name,pin in priorities)
