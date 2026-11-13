import math,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_loaded_wire_prior import fit,predict

def sample(n,y,weight=1):return dict(driver_class='lut',logical_fanout=n,mean_setup_wire_ns=y,weight=weight)

def test_weighted_model_recovers_known_relation_and_monotonic_load():
    model=fit([sample(n,.2+.3*math.log2(1+n),1/n) for n in (1,3,7,15)])
    assert model['slope']==pytest.approx(.3)
    assert predict(model,'LUT4',31)==pytest.approx(1.7)
    assert predict(model,'IBUF',2)==.672

def test_nonpositive_correlation_does_not_reward_extra_fanout():
    model=fit([sample(1,2),sample(7,1)])
    assert predict(model,'LUT2',1)==predict(model,'LUT2',7)

def test_invalid_data_rejects_and_weights_change_estimate():
    for row in [sample(0,1),sample(1,float('nan')),sample(1,1,0)]:
        with pytest.raises(ValueError):fit([row])
    model=fit([sample(1,1,9),sample(1,3,1)])
    assert predict(model,'LUT1',1)==pytest.approx(1.2)
    model['slope']=float('nan')
    with pytest.raises(ValueError):predict(model,'LUT1',1)

def test_optional_prior_changes_only_setup_feedback_and_preserves_native_context():
    import copy
    from test_heterogeneous_timing import fixture
    d,native,engine=fixture();before=copy.deepcopy(native)
    old_graph,old,_=engine.evaluate(d)
    engine.wire_prior=dict(slope=0.,intercepts={'register':1.2,'alu':1.2,'lut':1.2})
    new_graph,new,info=engine.evaluate(d)
    assert new['setup']['wns']<old['setup']['wns']
    assert new_graph['hold']==old_graph['hold']
    assert native==before and not info['native_routed_timing']
