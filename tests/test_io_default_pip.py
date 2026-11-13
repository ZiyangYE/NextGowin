import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import ownpr_io_default_pip as profile

def fixture(monkeypatch):
    monkeypatch.setattr(profile,'calibration',lambda:'checked')
    grid=[[0]*182 for _ in range(90)];grid[89][181]=246
    mask={(6,i):int(i==0) for i in range(10)};table={('F6','N260'):mask.copy(),('VCC','N260'):mask.copy()}
    return SimpleNamespace(grid=grid),SimpleNamespace(template=lambda typ:table),table

def test_zero_mask_is_scoped_to_calibrated_site(monkeypatch):
    d,c,t=fixture(monkeypatch);r=profile.requirements(d,c,(89,181,'Q6','N260'))
    assert len(r)==10 and set(r.values())=={0}
    assert profile.requirements(d,c,(88,181,'Q6','N260')) is None

@pytest.mark.parametrize('bad',['type','explicit','mask'])
def test_changed_native_selector_rejects(monkeypatch,bad):
    d,c,t=fixture(monkeypatch)
    if bad=='type':d.grid[89][181]=247
    elif bad=='explicit':t['Q6','N260']={}
    else:t['F6','N260'].pop((6,0))
    with pytest.raises(ValueError):profile.requirements(d,c,(89,181,'Q6','N260'))
