import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_lut_replication import replicate
from ownpr_image_storage import load_json,sha
from ownpr_device import Device


def test_replica_collapses_to_original_graph_without_changing_state_or_pins():
    base=ROOT/'results/vexriscv_own_hard_place_007';design=load_json(base/'packed.json');placed=load_json(base/'placement.json');before=copy.deepcopy(design)
    sinks=[('ng_coopt_1605','I2'),('ng_coopt_2054','I2')]
    new,layout,change=replicate(design,placed,Device(),'ng_coopt_1083',sinks)
    assert design==before and new['modules']['top']['ports']==design['modules']['top']['ports']
    original=design['modules']['top']['cells'];cells=new['modules']['top']['cells'];clone=cells[change['replica']]
    assert clone['parameters']==original[change['source']]['parameters']
    for pin,values in clone['connections'].items():
        if pin!='F':assert values==original[change['source']]['connections'][pin]
    for name,cell in cells.items():
        if name==change['replica']:continue
        reverted=copy.deepcopy(cell)
        reverted['connections']={p:[change['old_bit'] if b==change['new_bit'] else b for b in bs] for p,bs in cell['connections'].items()}
        assert reverted==original[name]
    assert layout['fixed_io']==placed['fixed_io'] and layout['hard_clusters']==placed['hard_clusters']
    assert all(layout['clusters'][n]==v for n,v in placed['clusters'].items())
    assert tuple(change['site']) not in {(r['row'],r['column'],r['slot']) for r in placed['clusters'].values()}


def test_real_replication_has_hash_bound_cec_evidence():
    folder=ROOT/'results/vexriscv_lut_replication_001';m=load_json(folder/'manifest.json')
    assert m['netlist_sha256']==sha(folder/'packed.json') and m['ordinary_clusters']==3686
    assert m['replication']['lut_growth']==1
    for index,row in enumerate(m['proof']):
        assert row['cec']['equivalent'] and row['cec']['inputs_unchanged']
        assert sha(folder/f'cec/module_{index}/before.blif')==row['cec']['reference_sha256']
        assert sha(folder/f'cec/module_{index}/after.blif')==row['cec']['candidate_sha256']


def test_invalid_replica_sink_is_rejected():
    base=ROOT/'results/vexriscv_own_hard_place_007';d=load_json(base/'packed.json');p=load_json(base/'placement.json')
    with pytest.raises(ValueError,match='logical edge'):replicate(d,p,Device(),'ng_coopt_1083',[('ng_coopt_1605','I0')])
    with pytest.raises(ValueError,match='duplicate'):replicate(d,p,Device(),'ng_coopt_1083',[('ng_coopt_1605','I2')]*2)


def test_hard_binding_probe_uses_the_actual_serialized_candidate():
    from ownpr_hard_architecture import HardArchitecture
    from ownpr_native_netlist import emit
    placement=ROOT/'results/vexriscv_lut_replication_003';folder=ROOT/'results/vexriscv_replica_hard_bindings_003';meta=load_json(folder/'manifest.json')
    design=load_json(placement/'packed.json');model=HardArchitecture();text,mapping=emit(design,hard=model)
    assert text==(folder/'probe.vg').read_text() and mapping==load_json(folder/'name_map.json')
    assert sha(folder/'probe.vg')==meta['request']['source_sha256'] and sha(folder/'name_map.json')==meta['request']['name_map_sha256']
    assert len(model.selected_bindings(design,load_json(placement/'placement.json'),folder)['cells'])==14


def test_tmds_selector_replica_preserves_dedicated_macro_links():
    from ownpr_mux_macros import validate
    base=ROOT/'results/tmds_feedback_moves_002/round_003/placement';d=load_json(base/'packed.json');p=load_json(base/'placement.json');dev=Device()
    new,layout,change=replicate(d,p,dev,'ng_coopt_243',[('ng_coopt_325','S0')])
    before=validate(d,p,dev);after=validate(new,layout,dev)
    assert before['links']==after['links'] and before['macros']==after['macros']
    assert layout['wide_macros']==p['wide_macros'] and layout['mux_clusters']==p['mux_clusters']
    assert new['modules']['top']['cells']['ng_coopt_325']['connections']['S0']==[change['new_bit']]
    for n,c in d['modules']['top']['cells'].items():
        restored=copy.deepcopy(new['modules']['top']['cells'][n])
        restored['connections']={pin:[change['old_bit'] if b==change['new_bit'] else b for b in bits] for pin,bits in restored['connections'].items()}
        assert restored==c
    with pytest.raises(ValueError,match='logical edge'):replicate(d,p,dev,'ng_coopt_243',[('ng_coopt_325','I0')])


def test_synchronous_enable_copy_is_cec_equivalent_and_keeps_other_state_fields(tmp_path):
    from native_boundary_cec import prove
    base=ROOT/'results/vexriscv_register_place_004';d=load_json(base/'packed.json');p=load_json(base/'placement.json');cells=d['modules']['top']['cells']
    ff='$auto$ff.cc:337:slice$47576';bit=cells[ff]['connections']['CE'][0]
    driver=next(n for n,c in cells.items() if c['connections'].get('F')==[bit]);dev=Device()
    new,placed,change=replicate(d,p,dev,driver,[(ff,'CE')])
    assert change['synchronous_enables_rewired']==[ff] and change['state_cells_changed'] and not change['state_storage_changed']
    assert all(placed['clusters'][n]==r for n,r in p['clusters'].items()) and placed['hard_clusters']==p['hard_clusters'] and placed['fixed_io']==p['fixed_io']
    for n,c in cells.items():
        if c['type'].startswith('DFF'):
            other=copy.deepcopy(new['modules']['top']['cells'][n])
            if n==ff:other['connections']['CE']=[bit]
            assert other==c
    assert all(r['cec']['equivalent'] for r in prove(d,new,tmp_path/'cec',120))
    for pin in ('CLK','RESET','D'):
        with pytest.raises(ValueError,match='logical edge'):replicate(d,p,dev,driver,[(ff,pin)])
