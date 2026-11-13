import copy,sys,math,shutil
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json
from ownpr_bidir_timing import analyze
from build_native_bidir_model import pairs_by_pin
from ownpr_full_fanout_alias import certify
from evaluate_fixed_clock_rewire import evaluate


def test_full_internal_rise_fall_setup_hold_against_native_states():
    m=load_json(ROOT/'results/vexriscv_87_3_bidir_model_001/model.json');native=pairs_by_pin(load_json(ROOT/'results/vexriscv_87_3_time_states_002/pin_feedback.json'))
    for early in (False,True):
        g=m['early' if early else 'late'];terms=m['hold_requirements' if early else 'setup_requirements'];r=analyze(g,terms,early);errors=[]
        for n,row in native.items():
            if n not in r['arrival'] or any(len(p)!=2 for p in row['transition_pairs']):continue
            for tr,p in enumerate(row['transition_pairs']):
                v=p[int(early)]
                if n not in g['seeds']:errors.append(abs(r['arrival'][n][tr]-v['arrival_ns']))
                if n not in terms and abs(v['required_ns'])<1e9 and math.isfinite(r['required'][n][tr]):errors.append(abs(r['required'][n][tr]-v['required_ns']))
        assert len(errors)>80000 and max(errors)<(.0002 if not early else .000003)
        assert r['violations']==(0 if early else 370)


def test_wrong_native_pair_semantics_rejected():
    d=load_json(ROOT/'results/vexriscv_87_3_time_states_002/pin_feedback.json');bad=copy.deepcopy(d)
    row=next(r for r in bad['pins'] if r['instance']=='$auto$ff.cc:337:slice$47248' and r['pin']=='Q')
    row['groups'][0]['states'][0]['pairs'][0]['arrival_ns']+=1.
    with pytest.raises(ValueError,match='semantics'):pairs_by_pin(bad)


def test_all_vector_receivers_retained_and_false_alias_rejected(tmp_path):
    b=ROOT/'results/vexriscv_frontier_partition_place_003';p=ROOT/'results/vexriscv_full_fanout_place_002';rows=load_json(p/'assignment.json')
    assert sum(r['total_receivers'] for r in rows)==337
    assert any('index' in s for r in rows for s in r['retained'])
    assert all(sum(r['after_loads'].values())==r['total_receivers']==len(r['allocations'])+len(r['retained']) for r in rows)
    aliases=load_json(p/'manifest.json')['register_aliases'];aliases['ng_ownpr_region_20_0']='$auto$ff.cc:337:slice$53408'
    with pytest.raises(ValueError):certify(load_json(b/'packed.json'),load_json(p/'packed.json'),aliases,tmp_path/'false_alias')


def test_relocation_cannot_reuse_fixed_clock_requirements(tmp_path):
    source=ROOT/'results/vexriscv_full_fanout_place_002'
    for f in ('packed.json','placement.json','manifest.json'):shutil.copyfile(source/f,tmp_path/f)
    p=tmp_path/'placement.json';text=p.read_text();p.write_text(text+'\n')
    with pytest.raises(ValueError,match='relocation'):evaluate(ROOT/'results/vexriscv_87_3_bidir_model_001',tmp_path,tmp_path,tmp_path)
