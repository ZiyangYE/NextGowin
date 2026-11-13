import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import ownpr_lw_fixed_profile as profile

def test_fixed_profile_does_not_extrapolate_to_other_spans(monkeypatch):
    monkeypatch.setattr(profile,'calibration',lambda:'verified')
    grid=[[0]*153 for _ in range(100)];grid[99][152]=40;grid[97][152]=17;d=SimpleNamespace(grid=grid)
    assert profile.fixed(d,(100,153,282),(98,153,286))
    assert profile.fixed(d,(98,153,288),(98,153,267))
    assert not profile.fixed(d,(100,154,282),(98,154,286))
    grid[97][152]=18
    with pytest.raises(ValueError):profile.fixed(d,(98,153,288),(98,153,267))
