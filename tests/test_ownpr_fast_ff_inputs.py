import copy,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_fast_ff_inputs import transform
from ownpr_image_storage import load_json,sha


def test_fast_ff_identity_mapping_preserves_function_and_every_ff():
    from ownpr_placement_feedback import load
    p=ROOT/'results/tmds_wide_replica_003/placement';d=load_json(p/'packed.json');placed=load_json(p/'placement.json');before=copy.deepcopy(d)
    weights,_=load(ROOT/'results/tmds_102_6mhz_001/export/native',d,relative_window_ns=1.)
    new,changes=transform(d,placed,weights);assert changes and d==before
    original=d['modules']['top']['cells'];cells=new['modules']['top']['cells'];changed={x['lut'] for x in changes}
    assert set(cells)==set(original) and new['modules']['top']['ports']==d['modules']['top']['ports']
    for n,c in original.items():
        if n not in changed:assert cells[n]==c
    for row in changes:
        a=original[row['lut']];b=cells[row['lut']];assert a['connections'][row['old_pin']]==b['connections']['I3'] and a['connections']['F']==b['connections']['F']
        expected=0x00ff if row['inverting'] else 0xff00
        assert int(b['parameters']['INIT'],2)==expected
        for value in (0,1):assert ((int(a['parameters']['INIT'],2)>>(value<<int(row['old_pin'][1:])))&1)==((expected>>(value<<3))&1)


def test_native_standalone_routes_and_wide_probe_are_bounded_evidence():
    p=ROOT/'results/native_wide_ff_routes_003';meta=load_json(p/'manifest.json');assert sha(p/'routes.json')==meta['routes_sha256']
    d=load_json(p/'routes.json');assert d['saved_physical_fields_restored'] and not d['production_enabled'] and len(d['records'])==80
    for row in d['records']:
        if row['driver']=='external':assert row['data_names']==['D'+str(row['ff_slot'])] and not row['direct']
        if row['driver']=='paired':assert row['direct'] and not row['data_points']
    matched=[r for r in d['records'] if (r['driver'],r['ff_slot']) in (('5',0),('6',1),('7',3))]
    assert len(matched)==6 and all(any(e['edge_present'] for e in r['source_direct_fanouts_to_data']) for r in matched)
    data=load_json(ROOT/'results/native_ff_data_encoding_003/comparison.json');assert data['full_payload_restored'] and data['standalone_equals_flagged']
    assert data['buffered_standalone_equals_paired_i3'] and data['buffered_standalone_flag_equals_paired_i3']
    words={r['label']:r['decoded_lut_init_hex'] for r in data['records']};assert words['paired_i0']=='AAAA' and words['paired_i3']=='FF00' and words['standalone']=='0000'
