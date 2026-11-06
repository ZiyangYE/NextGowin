import random,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_router import site_estimator


def test_cached_site_envelope_matches_original_nodewise_heuristic():
    rng=random.Random(74)
    goals=[((rng.randrange(12),rng.randrange(15)),rng.random()*3) for _ in range(500)]
    for scale in (0.,.001,.0025,.05):
        estimate=site_estimator(goals,scale)
        for r in range(18):
            for c in range(20):
                expected=min(scale*(abs(r-y)+abs(c-x))+arrival for (y,x),arrival in goals)
                assert estimate((r,c))==expected
                assert estimate([r,c])==expected


def test_tree_growth_gets_a_new_envelope():
    old=site_estimator([((0,0),0.)],.05)
    assert old((10,10))==1.
    new=site_estimator([((0,0),0.),((10,10),.2)],.05)
    assert new((10,10))==.2 and old((10,10))==1.
