import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from audit_native_hard_resources import audit


def test_hard_sites_and_dsp_binding_are_independently_cross_checked():
    r=audit(ROOT/'results/native_hard_site_inventory_001',ROOT/'results/native_vexriscv_hard_resources_001',ROOT/'results/native_dsp_binding_probe_002',ROOT/'results/coopt_vexriscv_mapping_003/05_netlist_retry/final.json')
    assert r['site_counts']=={'bsram':340,'new_dsp':298} and r['dsp_active_pins']==93 and r['dsp_cross_checked_samples']==6
    assert len(r['memory_observations'])==10
    assert any(x['multi_node_pins'] for x in r['memory_observations'])
    assert {p['width']:p['active_local_pins'] for p in r['independently_bound_memory_profiles']}=={18:116,9:78,36:98}
    assert r['independently_bound_memory_profiles'][-1]['shared_ce_aliases'][0]['pin']=='CEB'
    assert not r['production_packer_enabled'] and not r['configuration_verified'] and not r['hard_resource_sta_verified']
