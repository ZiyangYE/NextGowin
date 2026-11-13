import sys,copy
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_usercode import observe,compare_words,check_policy
from audit_native_encoding_probe import payload


def test_native_config_controls_change_only_the_documented_usercode_word():
    base=ROOT/'results/fair_native_baseline_trial_021/official_cli_cohort_001/int2float/project/baseline/impl/pnr/baseline.fs'
    original=payload(base)
    for label,value in [('cfg_default',0x94eb),('usercode_12345678',0x12345678),('usercode_89abcdef',0x89abcdef)]:
        path=ROOT/'results/official_payload_identity_trial_022'/('int2float_'+label)/'probe.fs'
        assert observe(path)['usercode']==value
        r=compare_words(original,payload(path))
        assert r['configuration_and_other_commands_identical']
        assert r['full_payload_identical']==(label=='cfg_default')


def test_real_payload_data_opcode_width_and_other_footer_mutations_reject():
    path=ROOT/'results/official_payload_identity_trial_022/int2float_cfg_default/probe.fs'
    words=payload(path)
    for i in (12,len(words)-6,len(words)-5,len(words)-3):
        changed=words.copy();changed[i]=('1' if changed[i][0]=='0' else '0')+changed[i][1:]
        with pytest.raises(ValueError):compare_words(words,changed)
    changed=words.copy();changed[-5]='0'+changed[-5]
    with pytest.raises(ValueError):compare_words(words,changed)


def test_native_policy_rejects_zero_as_default_and_wrong_explicit_claim(tmp_path):
    base=ROOT/'results/official_payload_identity_trial_022'
    with pytest.raises(ValueError,match='differs'):
        check_policy(ROOT/'results/fair_native_baseline_trial_021/int2float_official_core_all_calibration_001/probe.fs',base/'cfg_default.cfg')
    cfg=tmp_path/'wrong.cfg';cfg.write_text('set user_code = 0x12345679\n')
    with pytest.raises(ValueError,match='differs'):
        check_policy(base/'int2float_usercode_12345678/probe.fs',cfg)
    cfg.write_text('set user_code = 0x12345678\nset user_code = 0x12345678\n')
    with pytest.raises(ValueError,match='Duplicate'):check_policy(base/'int2float_usercode_12345678/probe.fs',cfg)
