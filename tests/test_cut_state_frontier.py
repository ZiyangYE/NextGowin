import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json
from ownpr_pin_feedback import load
from ownpr_device import Device
from ownpr_cut_state_frontier import candidates,apply
from ownpr_register_equivalence import prove_replicas


def fixture():
    source=ROOT/'results/vexriscv_state_frontier_002';mapped=ROOT/'results/vexriscv_shared_wide_001/candidate'
    before=load_json(source/'candidate/packed.json');design=load_json(mapped/'packed.json');layout=load_json(mapped/'placement.json');changes=load_json(mapped/'changes.json')
    budgets,_=load(ROOT/'results/vexriscv_shared_wide_001/pin_feedback',source/'qualification/export/native')
    return before,design,layout,changes,budgets,1000/67.9-1000/90


def test_new_macro_input_copy_has_complete_combined_inductive_proof(tmp_path):
    before,d,p,changes,budgets,shift=fixture();original=copy.deepcopy((d,p))
    after,placed,plan=apply(before,d,p,changes,budgets,Device(),shift,count=1)
    assert len(plan['register_replicas'])==1 and len(plan['selected'])==1
    row=plan['selected'][0];assert row['parent']=='$auto$ff.cc:337:slice$53406' and row['old_span']==61 and row['new_span']<61
    assert row['target_input_headroom_ns']['D']>8 and set(plan['register_replicas'].values())<set(before['modules']['top']['cells'])
    assert (d,p)==original and placed['fixed_io']==p['fixed_io'] and placed['hard_clusters']==p['hard_clusters']
    assert all(placed['clusters'][n]==v for n,v in p['clusters'].items() if v.get('ff'))
    proof=prove_replicas(before,after,plan['register_replicas'],tmp_path/'combined',120)
    assert proof['status']=='register_replica_invariant_proved'


def test_target_tight_or_missing_input_slack_is_not_admitted():
    before,d,p,changes,budgets,shift=fixture();parent='$auto$ff.cc:337:slice$53406';modified=copy.deepcopy(budgets)
    modified[parent,'D']['original_setup_slack_ns']=shift+.1
    options,rejected=candidates(before,d,p,changes,modified,shift)
    assert not any(r['parent']==parent for r in options)
    assert any(r['parent']==parent and 'too tight' in r['reason'] for r in rejected)
    modified.pop((parent,'D'));options,rejected=candidates(before,d,p,changes,modified,shift)
    assert not any(r['parent']==parent for r in options)
    assert any(r['parent']==parent and 'Missing' in r['reason'] for r in rejected)


def test_frontier_limits_reject_nonfinite_period_and_unbounded_growth():
    before,d,p,changes,budgets,shift=fixture()
    with pytest.raises(ValueError):candidates(before,d,p,changes,budgets,float('nan'))
    with pytest.raises(ValueError):candidates(before,d,p,changes,budgets,shift,count=9)
