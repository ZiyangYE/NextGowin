import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_arrival_graph import ioreg_clock_to_q


def test_instance_queries_are_used_without_assuming_slice_ff_equivalence():
    p={'ioreg_clusters':{'ff':{'site':[110,49,0]}}};a={'ff':'native_ff'}
    d={'registers':[dict(instance='native_ff',site=[0,110,49,1],clock_positive=True,queries=[dict(pin='Q',selector=1,maximum=True,values=[.2,.3,.4,.5])])]}
    assert ioreg_clock_to_q(d,p,a)=={'ff':[.4,.5]}
    with pytest.raises(ValueError,match='Incomplete'):ioreg_clock_to_q({'registers':[]},p,a)
    for mutate in [lambda r:r.update(clock_positive=False),lambda r:r.update(site=[0,109,49,1]),lambda r:r['queries'].append(copy.deepcopy(r['queries'][0])),lambda r:r['queries'][0].update(values=[0,0,float('nan'),0])]:
        bad=copy.deepcopy(d);mutate(bad['registers'][0])
        with pytest.raises(ValueError):ioreg_clock_to_q(bad,p,a)
