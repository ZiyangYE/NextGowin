import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_frontier_partition import partitions
from ownpr_image_storage import load_json,read_text
from routing_path_diagnosis import diagnose
from ownpr_register_equivalence import prove_replicas,collapse


def source():
    base=ROOT/'results/vexriscv_internal_state_place_002';sta=ROOT/'results/vexriscv_internal_state_trial_001/export/native'
    return load_json(base/'packed.json'),load_json(base/'placement.json'),diagnose(read_text(sta/'probe.tr')),{v:k for k,v in load_json(sta/'name_map.json')['instances'].items()}


def test_disjoint_receiver_partitions_and_synchronizer_exclusion():
    d,l,paths,reverse=source();rows=partitions(d,l,paths,reverse)
    assert len(rows)==6
    parent='$auto$ff.cc:337:slice$47248';chosen=[r for r in rows if r['source']==parent]
    assert len(chosen)==2 and set(map(tuple,chosen[0]['sinks'])).isdisjoint(map(tuple,chosen[1]['sinks']))
    assert len(set(chosen[0]['covered_paths'])|set(chosen[1]['covered_paths']))>=9
    bad=copy.deepcopy(d);bad['modules']['top']['cells'][parent]['attributes']['ASYNC_REG']='1'
    assert all(r['source']!=parent for r in partitions(bad,l,paths,reverse))
    bad=copy.deepcopy(d);q=bad['modules']['top']['cells'][parent]['connections']['Q']
    bad['modules']['top'].setdefault('netnames',{})['marked_synchronizer']={'bits':q,'attributes':{'ASYNC_REG':'1'}}
    assert all(r['source']!=parent for r in partitions(bad,l,paths,reverse))


def test_actual_partition_preserves_old_state_and_rejects_bad_clone_enable(tmp_path):
    old,layout,_,_=source();base=ROOT/'results/vexriscv_frontier_partition_place_001'
    new=load_json(base/'packed.json');placed=load_json(base/'placement.json');m=load_json(base/'manifest.json')
    assert placed['fixed_io']==layout['fixed_io'] and placed['hard_clusters']==layout['hard_clusters']
    assert placed['arithmetic_clusters']==layout['arithmetic_clusters'] and placed['mux_clusters']==layout['mux_clusters']
    assert new['modules']['top']['ports']==old['modules']['top']['ports']
    for n,c in old['modules']['top']['cells'].items():
        if c['type'].startswith('DFF'):assert new['modules']['top']['cells'][n]==c
    assert m['proof']['status']=='register_replica_invariant_proved'
    clone,parent=next(iter(m['register_replicas'].items()));bad=copy.deepcopy(new)
    assert bad['modules']['top']['cells'][clone]['connections']['CE']==old['modules']['top']['cells'][parent]['connections']['CE']
    bad['modules']['top']['cells'][clone]['connections']['CE']=['0']
    with pytest.raises((ValueError,RuntimeError)):
        prove_replicas(old,bad,m['register_replicas'],tmp_path/'bad_enable',120)
    bad=copy.deepcopy(new);q=bad['modules']['top']['cells'][clone]['connections']['Q']
    bad['modules']['top']['netnames']['marked_synchronizer']={'bits':q,'attributes':{'ASYNC_REG':'1'}}
    with pytest.raises(ValueError,match='Protected'):collapse(old,bad,m['register_replicas'])
    bad=copy.deepcopy(new)
    for n in (parent,clone):bad['modules']['top']['cells'][n]['parameters']['GSREN']='0'
    with pytest.raises(ValueError,match='unmodeled'):collapse(old,bad,m['register_replicas'])
    bad=copy.deepcopy(new);kept=next(n for n,r in old['modules']['top']['netnames'].items() if r.get('attributes',{}).get('keep','').endswith('1'))
    del bad['modules']['top']['netnames'][kept]
    with pytest.raises(ValueError,match='kept original net'):collapse(old,bad,m['register_replicas'])
