import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_shared_ff_calibration import audit,verify,predict
from ownpr_ff_configuration import independent_ff_groups


def data():return json.loads((ROOT/'results/native_ff_shared_controls_002/ff_attributes.json').read_text())['shared_records']


def test_all_common_control_profiles_match_but_packer_stays_closed():
    r=audit(ROOT/'results/native_ff_shared_controls_002')
    assert r['cases']==384 and not r['configured_pair_verified'] and not r['production_packer_enabled']
    with pytest.raises(ValueError):independent_ff_groups(data()[0]['ffs'])


def test_mismatched_profiles_or_unobserved_slots_are_rejected():
    profiles=copy.deepcopy(data()[0]['ffs']);profiles[1]['ce']='one'
    with pytest.raises(ValueError,match='match'):predict(profiles)
    profiles=copy.deepcopy(data()[0]['ffs']);profiles[0]['slot']=6;profiles[1]['slot']=7
    with pytest.raises(ValueError,match='slots'):predict(profiles)


def test_independent_data_and_pin_identity_cannot_be_inferred_from_matching_attributes():
    rows=data();rows[0]['data_nets_distinct']=False
    with pytest.raises(ValueError,match='independent data'):verify(rows)
    rows=data();rows[0]['native_pin_nodes'][1]['Q']=rows[0]['native_pin_nodes'][0]['Q']
    with pytest.raises(ValueError,match='not independent'):verify(rows)
    rows=data();rows[1]=copy.deepcopy(rows[0])
    with pytest.raises(ValueError,match='Duplicate'):verify(rows)
