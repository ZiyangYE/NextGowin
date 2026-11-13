import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_bidir_timing import analyze
from diagnose_bidir_paths import witness


def test_witness_respects_injected_seed_over_smaller_incoming_arrival():
    g=dict(nodes=['a','b','c'],seeds={'a':[0.,0.],'b':[10.,12.]},edges=[
        dict(source='a',destination='b',delay=[1.,1.],transitions=[(0,0),(1,1)],kind='wire'),
        dict(source='b',destination='c',delay=[3.,2.],transitions=[(0,1),(1,0)],kind='cell')])
    m=analyze(g,{'c':[20.,20.]});r=witness(g,m,'c',{})
    assert r['source']=='b' and r['arrival_ns']==15. and r['source_arrival_ns']==12.
    assert r['wire_sum_ns']==0. and r['nonwire_sum_ns']==3. and r['reconstruction_error_ns']==0.
