import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_carry_simplify import simplify
from native_boundary_cec import prove
from ownpr_clock_slack import compile_model
from audit_ownpr_extended_sta import audit
from ownpr_alu_configuration import Mode2Configuration
from ownpr_device import Device
from ownpr_route_eco import first_hold_wire


def test_sign_extended_carry_reduction_is_equivalent_and_keeps_observed_sums(tmp_path):
    d=json.loads((ROOT/'results/coopt_tmds_initial_001/input.json').read_text());before=copy.deepcopy(d)
    new,removed=simplify(d)
    assert len(removed)==23 and d==before
    assert all(r['cec']['equivalent'] for r in prove(d,new,tmp_path/'cec',60))
    protected=removed[0]['instance'];summ=d['modules']['top']['cells'][protected]['connections']['SUM'][0]
    d['modules']['top']['netnames']['observed_sum']={'bits':[summ],'attributes':{'keep':'1'}}
    new,removed=simplify(d)
    assert protected in new['modules']['top']['cells']


def test_synchronous_only_clock_feedback_has_empty_async_sections():
    model=compile_model(ROOT/'results/coopt_int2float_rebuild_001/placement',ROOT/'results/coopt_int2float_joint_001/round_001/eco_export/native')
    assert model['constraints']['recovery']==model['constraints']['removal']==[]


def test_synchronous_alu_report_requires_only_present_ordinary_corner_sections():
    result=audit(ROOT/'results/coopt_tmds_export_002/native',ROOT/'results/coopt_tmds_place_003')
    assert result['coverage']['Recovery']['endpoints']==result['coverage']['Removal']['endpoints']==0
    assert set(result['ordinary_tnet_rows'])=={'Setup','Hold'}


def test_new_alu_profiles_are_native_calibrated():
    model=Mode2Configuration(Device());report=json.loads((ROOT/'results/native_alu_encoding_007/alu_configuration_audit.json').read_text())
    assert report['profiles']==10 and report['reference_cells_matched']==48 and report['full_payload_restored']
    for row in report['records']:assert model.word(2,row['profile'])==row['truth_word']


def test_multisink_hold_branch_uses_the_reported_data_path():
    text=(ROOT/'results/coopt_tmds_checkpoint_002/export/native/probe.tr').read_text()
    assert first_hold_wire(text,'own_i_0256/I','own_i_0059/D')=='own_i_0207/I3'
    with pytest.raises(ValueError,match='Ambiguous or missing'):first_hold_wire(text,'own_i_0256/I','own_i_0072/D')
