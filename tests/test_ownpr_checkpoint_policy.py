import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from run_ownpr_checkpoint import routing_policy,require_reuse_policy


def test_missing_legacy_policy_requires_explicit_values_and_keeps_zero():
    with pytest.raises(ValueError,match='source-audited'):require_reuse_policy({},None,None,None)
    with pytest.raises(ValueError,match='guide_edge_delay'):require_reuse_policy(dict(search_bias=30,root_arrival_weight=1),None,None,None)
    require_reuse_policy({},30,0,.05)
    require_reuse_policy(dict(search_bias=30,root_arrival_weight=1,guide_edge_delay_ns_surrogate=.05),None,None,None)


def test_explicit_cpu_policy_survives_a_routing_eco_without_inherited_search_fields():
    assert routing_policy({},30,0,.05,True)==dict(search_bias=30,root_arrival_weight=0,guide_edge_delay_ns_surrogate=.05,guided_corridor=True)


def test_inherited_policy_is_preserved_and_explicit_zero_is_an_override():
    source=dict(search_bias=20,root_arrival_weight=.5,guide_edge_delay_ns_surrogate=.1,guided_corridor=True);old=copy.deepcopy(source)
    assert routing_policy(source)==source
    assert routing_policy(source,root_arrival_weight=0)['root_arrival_weight']==0
    assert source==old


@pytest.mark.parametrize('values',[(31,0,.05),(30,float('nan'),.05),(30,0,0),(30,0,float('inf'))])
def test_invalid_search_settings_fail_closed(values):
    with pytest.raises(ValueError,match='bounds'):routing_policy({},*values)
