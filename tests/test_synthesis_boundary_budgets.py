import sys,math
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_synthesis_boundary_budgets import minimum_required


def test_native_one_edge_control_uses_real_requirement_not_sentinel():
    assert minimum_required([7.57,1e10])==7.57
    assert minimum_required([7.83,7.82])==7.82


@pytest.mark.parametrize('values',[[1e10,1e10],[-1e10,7.5],[float('nan'),7.5],[float('inf'),7.5],[2e10,7.5],[7.5]])
def test_uncovered_or_unrecognized_native_transition_is_rejected(values):
    with pytest.raises(ValueError):minimum_required(values)
