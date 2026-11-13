import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_cut_competition import score_order

def test_data_branch_can_outpace_fast_selector():
    intrinsic=lambda index,width:.2 if index==width-1 else 1.4
    arrival,winner,_=score_order([1,2],{1:5.,2:5.5},{1:1.,2:0.},intrinsic)
    assert winner==1 and abs(arrival-7.4)<1e-9
    improved,_,_=score_order([2,1],{1:5.,2:5.5},{1:1.,2:0.},intrinsic)
    assert improved<arrival
