import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from native_io_extended_state import compare,LOGICAL_ATTRIBUTES,EXPECTED


def fixture():
    return dict(schema='native-extended-physical-io-observation-v1',dll_sha256=EXPECTED,physical_attribute_count=81,
        logical_attribute_indices=list(LOGICAL_ATTRIBUTES),attributes={str(i):str(i) for i in range(81)},
        io=[dict(instance='pin',site=[0,2,3,1],physical=[0]*81,physical_getter_matches_raw=True,logical_supported={str(i):0 for i in LOGICAL_ATTRIBUTES})])


def test_all81_physical_entries_are_compared_even_unnamed_tail():
    a=fixture();b=copy.deepcopy(a);assert compare(a,b)['physical_values_checked']==81
    b['io'][0]['physical'][80]=1;r=compare(a,b)
    assert r['status']=='extended_io_vectors_differ' and r['differences'][0]['attribute']==80


def test_supported_special_logical_field_is_compared():
    a=fixture();b=copy.deepcopy(a);b['io'][0]['logical_supported']['77']=3
    r=compare(a,b);assert r['differences'][0]['kind']=='logical'
    assert 16 not in LOGICAL_ATTRIBUTES and len(LOGICAL_ATTRIBUTES)==26


@pytest.mark.parametrize('bad',['missing_tail','duplicate','unbound','schema','logical','raw'])
def test_incomplete_unbound_or_unverified_observation_rejects(bad):
    a=fixture();b=copy.deepcopy(a)
    if bad=='missing_tail':b['io'][0]['physical'].pop()
    elif bad=='duplicate':b['io'].append(copy.deepcopy(b['io'][0]))
    elif bad=='unbound':b['io'][0]['site']=None
    elif bad=='schema':b['physical_attribute_count']=16
    elif bad=='logical':b['io'][0]['logical_supported']['16']=0
    else:b['io'][0]['physical_getter_matches_raw']=False
    with pytest.raises(ValueError):compare(a,b)


@pytest.mark.parametrize('capture_name',['pin_feedback.json','extended_io_state.json'])
def test_capture_hash_is_not_replaced_by_last_source_filename(monkeypatch,capture_name):
    import audit_extended_io_pair as auditor
    data=fixture();data['encoding_phase']='after_fresh_process_single_encoding'
    meta=dict(source='source',status='captured',vendor_placement_executed=False,vendor_routing_executed=False,
        bitstream_generated=True,extended_capture_file=capture_name,extended_capture_sha256='hash:'+capture_name,
        image_sha256='hash:probe.fs',input_sha256={'io_reference.json':'hash:io_reference.json'})
    legacy={'io':[dict(site=[0,2,3,1],physical=[0]*16,logical=[0]*16)]}
    def read(path):
        if path.parent.name=='capture' and path.name=='manifest.json':return meta
        if path.name==capture_name:return data
        if path.name=='io_state_after_encoding.json':return legacy
        raise AssertionError(path)
    monkeypatch.setattr(auditor,'load_json',read);monkeypatch.setattr(auditor,'sha',lambda p:'hash:'+p.name)
    monkeypatch.setattr(auditor,'payload',lambda p:[])
    _,receipt=auditor.admitted(Path('capture'))
    assert receipt['capture_sha256']=='hash:'+capture_name
