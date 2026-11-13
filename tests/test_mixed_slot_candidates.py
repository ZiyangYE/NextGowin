import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'experiments'))
from ownpr_image_storage import load_json
from ownpr_mixed_slot_candidates import discover
from analyze_ownpr_critical_wires import analyze


def test_discovery_uses_graph_not_fixed_instance_names_and_avoids_occupied_slots():
    p=ROOT/'results/vexriscv_mixed_wide_ff_preflight_001';r=ROOT/'results/vexriscv_mixed_wide_ff_trial_001'
    design=load_json(p/'packed.json');layout=load_json(p/'placement.json');ranking=analyze(r/'export/native',p,r/'route',128)
    before=discover(design,layout,ranking);assert before['candidates']
    occupied={(v['row'],v['column'],v['slot']) for v in layout['clusters'].values()}
    assert all(tuple(c['new_site']) not in occupied and c['span_gain']>0 for c in before['candidates'])
    old=before['candidates'][0]['lut'];new='renamed_ordinary_test_lut';d=copy.deepcopy(design);l=copy.deepcopy(layout);rank=copy.deepcopy(ranking)
    d['modules']['top']['cells'][new]=d['modules']['top']['cells'].pop(old)
    l['clusters'][new]=l['clusters'].pop(old);l['clusters'][new]['lut']=new
    for row in rank['ranked_nets']:
        if row['driver']['instance']==old:row['driver']['instance']=new
        row['endpoints']=[new+e[len(old):] if e.startswith(old+'/') else e for e in row['endpoints']]
    for path in rank['traces']:
        for edge in path['ordinary_wires']:
            if edge['endpoint'].startswith(old+'/'):edge['endpoint']=new+edge['endpoint'][len(old):]
    after=discover(d,l,rank)
    assert before['candidate_count']==after['candidate_count']
    expected=[{k:v for k,v in c.items() if k!='lut'} for c in before['candidates'] if c['lut']==old]
    actual=[{k:v for k,v in c.items() if k!='lut'} for c in after['candidates'] if c['lut']==new]
    assert expected and actual==expected
    with pytest.raises(ValueError):discover(design,layout,ranking,radius=100)


def test_critical_input_weight_rejects_observed_harmful_move():
    p=ROOT/'results/vexriscv_mixed_wide_ff_preflight_001';r=ROOT/'results/vexriscv_mixed_wide_ff_trial_001'
    design=load_json(p/'packed.json');layout=load_json(p/'placement.json');ranking=analyze(r/'export/native',p,r/'route',128)
    # This actual routed counterexample shortened F but made critical I2 slow.
    ordinary=discover(design,layout,ranking,limit=64)
    bad=('ng_coopt_2893','ng_ownpr_region_5_1',2)
    assert any((c['lut'],c['root'],c['slot'])==bad for c in ordinary['candidates'])
    weighted=discover(design,layout,ranking,priorities={('ng_coopt_2893','I2'):1.},limit=64)
    assert not any((c['lut'],c['root'],c['slot'])==bad for c in weighted['candidates'])


def test_sibling_receivers_do_not_inherit_the_worst_net_slack():
    from ownpr_mixed_slot_candidates import critical_scores
    ranking=dict(ranked_nets=[dict(net='n',driver=dict(instance='driver'),endpoints=['late/I0','early/I1'],worst_path_slack_ns=-3.,maximum_reported_wire_ns=2.)],traces=[
        dict(slack_ns=-3.,ordinary_wires=[dict(net='n',endpoint='late/I0',wire_delay_ns=2.)]),
        dict(slack_ns=-1.,ordinary_wires=[dict(net='n',endpoint='early/I1',wire_delay_ns=.4)])])
    scores=critical_scores(ranking)
    assert scores['driver']==(-3.,-2.) and scores['late']==(-3.,-2.)
    assert scores['early']==(-1.,-.4)
    ranking['traces'].pop()
    with pytest.raises(ValueError,match='coverage'):critical_scores(ranking)
