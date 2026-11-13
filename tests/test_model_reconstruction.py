import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_model_reference import validate_reconstruction
from ownpr_bidir_timing import analyze


def test_clean_wns_and_zero_tns_do_not_hide_wrong_noncritical_arc():
    graph=dict(nodes=['source','worst','noncritical'],seeds={'source':[0.,0.]},edges=[dict(source='source',destination=n,kind='wire',transitions=[(0,0),(1,1)],delay=[.1,.1]) for n in ('worst','noncritical')])
    model=dict(setup_requirements={'worst':[.2,.2],'noncritical':[10.,10.]},hold_requirements={'worst':[0.,0.],'noncritical':[0.,0.]})
    changed=copy.deepcopy(graph);changed['edges'][1]['delay']=[.5,.5]
    old=analyze(graph,model['setup_requirements']);new=analyze(changed,model['setup_requirements'])
    assert old['wns']==new['wns'] and old['tns']==new['tns']==0
    with pytest.raises(ValueError,match='full-graph'):validate_reconstruction(model,dict(setup=graph,hold=graph),dict(setup=changed,hold=graph))
    r=validate_reconstruction(model,dict(setup=graph,hold=graph),dict(setup=graph,hold=graph))
    assert r['setup']['maximum_node_errors_ns']['required']==0.
