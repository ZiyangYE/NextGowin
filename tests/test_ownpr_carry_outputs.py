import copy,sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_carry_outputs import materialize
from native_boundary_cec import functions,prove
from ownpr_carry_chains import extract


def test_tmds_terminal_carry_transform_preserves_hard_boundaries(tmp_path):
    d=json.loads((ROOT/'results/coopt_tmds_initial_001/input.json').read_text());before=copy.deepcopy(d)
    new,changes=materialize(d)
    assert len(changes)==3 and before==d
    assert all(not c['tail_loads'] for c in extract(new['modules']['top'])['chains'])
    assert all(p['cec']['equivalent'] for p in prove(d,new,tmp_path/'cec',60))
    for row in changes:
        alu=new['modules']['top']['cells'][row['alu']];truth=functions(alu)[0][2]
        assert (truth&1)==1 and ((truth>>8)&1)==0
