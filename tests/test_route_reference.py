import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_route_reference import check_boundary,clock_normalized,select
from ownpr_image_storage import load_json


def test_only_primary_period_may_differ_in_timing_contract():
    a='create_clock -name clk -period 10 [get_ports {clk}]\nset_input_delay -max 0 -clock clk [get_ports x]\n'
    assert clock_normalized(a)==clock_normalized(a.replace('period 10','period 12'))
    assert clock_normalized(a)!=clock_normalized(a.replace('-max 0','-max 1'))
    with pytest.raises(ValueError):clock_normalized('create_generated_clock -name derived')


def test_seed_cannot_move_io_or_hard_sites_or_change_ports():
    d={'modules':{'top':{'ports':{'a':{'direction':'input','bits':[2]}}}}};p={'fixed_io':{'a':[1,2]},'hard_clusters':{'dsp':[3,4]}}
    check_boundary(d,p,copy.deepcopy(d),copy.deepcopy(p))
    for field in ('fixed_io','hard_clusters'):
        changed=copy.deepcopy(p);changed[field]={}
        with pytest.raises(ValueError):check_boundary(d,p,d,changed)
    changed=copy.deepcopy(d);changed['modules']['top']['ports']={}
    with pytest.raises(ValueError):check_boundary(d,p,changed,p)


def test_real_reviewed_shared_route_can_seed_a_different_logic_baseline():
    base=load_json(ROOT/'results/vexriscv_state_frontier_002/qualified_replay_checkpoint.json')['designs'][0]
    state={k:ROOT/v for k,v in base['selected'].items()};contract={k:ROOT/v for k,v in base['constraints'].items()}
    routes,audit=select(ROOT/'results/vexriscv_shared_wide_001/backend_qualified_checkpoint.json','vexriscv',state,contract)
    assert routes==ROOT/'results/vexriscv_shared_wide_001/implementation/route'
    assert audit['logic_and_clock_from_primary_checkpoint'] and audit['reference_metrics']['native_constraints_satisfied']
