import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_device import Device,ROOT
from ownpr_image_storage import load_json
from ownpr_regional_replication import replicate
from ownpr_register_equivalence import prove_replicas


def test_register_can_directly_drive_a_replicated_wide_selector_frontier(tmp_path):
    base=ROOT/'results/vexriscv_measured_carry_005/candidate/placement'
    d=load_json(base/'packed.json');p=load_json(base/'placement.json');cells=d['modules']['top']['cells']
    source='$auto$ff.cc:337:slice$47569';sink='ng_coopt_1074'
    assert cells[sink]['connections']['S0']==cells[source]['connections']['Q']
    new,layout,change=replicate(d,p,Device(),source,[(sink,'S0')],allowed_registers={source},cluster_region=True)
    assert change['source_kind']=='state' and change['cone']==[]
    assert change['register_growth']==change['lut_growth']==1
    clone=next(iter(change['register_replicas']))
    assert new['modules']['top']['cells'][sink]['connections']['S0']==new['modules']['top']['cells'][clone]['connections']['Q']
    assert new['modules']['top']['cells'][source]==cells[source]
    old_ff=next(r for r in p['clusters'].values() if r.get('ff')==source)
    assert next(r for r in layout['clusters'].values() if r.get('ff')==source)==old_ff
    assert layout['fixed_io']==p['fixed_io'] and layout['hard_clusters']==p['hard_clusters']
    assert change['legalization']['displaced_registers']==0 and change['legalization']['displaced_units']<=1
    assert all(layout['clusters'][n]==r for n,r in p['clusters'].items() if r.get('ff'))
    proof=prove_replicas(d,new,change['register_replicas'],tmp_path/'proof')
    assert proof['status']=='register_replica_invariant_proved'
