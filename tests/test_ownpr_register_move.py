import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json
from ownpr_device import Device
from ownpr_register_move import apply,batch


@pytest.fixture(scope='module')
def source():
    p=ROOT/'results/vexriscv_critical_replica_004/placement'
    return load_json(p/'packed.json'),load_json(p/'placement.json'),Device()


def clock_endpoints(placed,device):
    pins=device.register_bindings_for('DFFRE')
    return {(r['row'],r['column'],pins[r['slot']]['CLK']) for r in placed['clusters'].values() if r.get('ff')}


def test_real_critical_pair_swap_preserves_every_other_site_and_clock_set(source):
    d,p,dev=source;before=copy.deepcopy(p);change=load_json(ROOT/'results/vexriscv_register_place_002/manifest.json')['register_move']
    after=apply(d,p,dev,change);names={change['lut'],change['swap_partner']['lut']}
    assert p==before and all(after['clusters'][n]==r for n,r in p['clusters'].items() if n not in names)
    assert all(after[k]==p[k] for k in ('fixed_io','hard_clusters','arithmetic_clusters'))
    assert clock_endpoints(after,dev)==clock_endpoints(p,dev)
    assert {(r['row'],r['column'],r['slot']) for r in after['clusters'].values()}=={(r['row'],r['column'],r['slot']) for r in p['clusters'].values()}
    assert after['clusters'][change['lut']]['ff']==change['ff'] and not change['clock_replan_required']


def test_free_register_relocation_requires_exactly_one_clock_endpoint_change(source):
    d,p,dev=source;change=load_json(ROOT/'results/vexriscv_register_place_001/manifest.json')['register_move'];after=apply(d,p,dev,change)
    old,new=clock_endpoints(p,dev),clock_endpoints(after,dev)
    assert len(new-old)==len(old-new)==1 and change['clock_replan_required']


@pytest.mark.parametrize('mutation',['occupied','slot','source','domain'])
def test_unsafe_register_candidates_are_rejected(source,mutation):
    original,p,dev=source;d=copy.deepcopy(original);c=copy.deepcopy(load_json(ROOT/'results/vexriscv_register_place_002/manifest.json')['register_move'])
    if mutation=='occupied':c['swap_partner']=None
    if mutation=='slot':c['new_site'][2]=0
    if mutation=='source':c['old_site'][0]+=1
    if mutation=='domain':d['modules']['top']['cells'][c['swap_partner']['ff']]['connections']['CLK']=[999999999]
    with pytest.raises(ValueError):apply(d,p,dev,c)


def test_batch_moves_disjoint_clusters_and_accounts_for_free_clock_endpoints(source):
    d,_,dev=source;p=load_json(ROOT/'results/vexriscv_register_place_002/placement.json')
    options=load_json(ROOT/'results/vexriscv_register_place_003/candidates.json');after,chosen,skipped=batch(d,p,dev,options,6)
    assert len(chosen)==6 and after==load_json(ROOT/'results/vexriscv_register_place_003/placement.json')
    touched=[name for c in chosen for name in ([c['lut'],c['swap_partner']['lut']] if c.get('swap_partner') else [c['lut']])]
    assert len(touched)==len(set(touched))
    old,new=clock_endpoints(p,dev),clock_endpoints(after,dev)
    assert len(old-new)==len(new-old)==2
    assert all(after[k]==p[k] for k in ('fixed_io','hard_clusters','arithmetic_clusters'))


def test_sta_guided_rollback_restores_only_the_offending_swap(source):
    from rollback_ownpr_register_batch import rollback
    d,_,dev=source;reference=load_json(ROOT/'results/vexriscv_register_place_002/placement.json');failed=load_json(ROOT/'results/vexriscv_register_place_003/placement.json')
    moves=load_json(ROOT/'results/vexriscv_register_place_003/manifest.json')['register_moves']
    repaired,removed,left=rollback(d,reference,failed,moves,{'$auto$ff.cc:337:slice$47401'})
    assert len(removed)==1 and len(left)==5 and repaired==load_json(ROOT/'results/vexriscv_register_place_004/placement.json')
    names={removed[0]['lut'],removed[0]['swap_partner']['lut']}
    assert all(repaired['clusters'][n]==(reference if n in names else failed)['clusters'][n] for n in failed['clusters'])
    assert clock_endpoints(repaired,dev)==clock_endpoints(failed,dev)


def test_rollback_does_not_treat_an_unmoved_endpoint_as_a_batch_member(source):
    from rollback_ownpr_register_batch import rollback
    d,_,_=source;reference=load_json(ROOT/'results/vexriscv_register_place_002/placement.json');failed=load_json(ROOT/'results/vexriscv_register_place_003/placement.json');moves=load_json(ROOT/'results/vexriscv_register_place_003/manifest.json')['register_moves']
    with pytest.raises(ValueError,match='No moved'):rollback(d,reference,failed,moves,{'$auto$ff.cc:337:slice$47416'})
