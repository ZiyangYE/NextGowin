import sys,copy,math
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json
from ownpr_arrival_graph import propagate
from ownpr_arrival_evaluator import ArrivalEvaluator


def test_compiled_real_graph_preserves_competitors_and_changed_arc_propagation():
    graph=load_json(ROOT/'results/clock_cone_joint_trial_020/int2float_clock_slack_001/clock_feedback.json')['graph']
    model=ArrivalEvaluator(graph)
    for overrides in ({},{i:[v+.4 for v in graph['edges'][i]['delay']] for i in range(0,len(graph['edges']),37)}):
        actual=model.evaluate(overrides);expected=propagate(**graph,overrides=overrides)[0]
        assert all(actual[model.ids[n]:model.ids[n]+2]==v for n,v in expected.items())
    with pytest.raises(ValueError):model.evaluate({len(graph['edges']):[0.,0.]})
    with pytest.raises(ValueError):model.evaluate({0:[-1.,0.]})


def test_unreachable_transition_stays_unreachable_and_seeds_are_not_mutated():
    graph=dict(nodes=['a','b'],seeds={'a':[1.,2.]},edges=[dict(source='a',destination='b',delay=[.4,.7],transitions=[(1,0)])])
    before=copy.deepcopy(graph);model=ArrivalEvaluator(graph)
    assert model.evaluate()[model.ids['b']:]==[2.4,-math.inf]
    assert graph==before
