import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from official_command_template import rebind


def test_actual_official_command_preserves_policy_and_binds_inputs(tmp_path):
    base=ROOT/'results/fair_native_baseline_trial_021/official_cli_cohort_001/int2float'
    template=base/'project/baseline/impl/pnr/cmd.do'
    source=base/'project/baseline/impl/gwsynthesis/baseline.vg'
    text,info=rebind(template,tmp_path,source,base/'fixed.cst',base/'clock.sdc','core_all',True)
    assert '-o\n' in text and '-init_all\n' in text
    assert '-convert_sdp32_36_to_sdp16_18\n' in text
    assert info['preserved_nonpath_options']['-global_freq']=='100.000'
    assert (tmp_path/'device.cfg').read_bytes()==(template.parent/'device.cfg').read_bytes()
    with pytest.raises(ValueError,match='policy differs'):
        rebind(template,tmp_path,source,base/'fixed.cst',base/'clock.sdc','core_inputs',True)
    changed=tmp_path/'bad.sdc';changed.write_text((base/'clock.sdc').read_text()+'\n# mutation\n')
    with pytest.raises(ValueError,match='input differs'):
        rebind(template,tmp_path,source,base/'fixed.cst',changed,'core_all',True)
