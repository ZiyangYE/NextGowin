import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import ownpr_primary_p33_profile as profile

def model(monkeypatch):
    monkeypatch.setattr(profile,'calibration',lambda:{})
    grid=[[0]*182 for _ in range(82)];grid[81][181]=276
    table={(4,26):{(2,129):0,(2,125):1},(11,26):{(2,129):1,(2,125):0},(22,26):{(2,129):1,(2,125):1}}
    m=SimpleNamespace(device=SimpleNamespace(grid=grid),table=lambda t,k:table,global_choice=lambda t,s,d:(54,88,{(20,58):1}),graph=SimpleNamespace(by_coordinate={(-1,-1,300):0}),indegree={0:1})
    return m,table

def test_default_selection_clears_every_competing_bit(monkeypatch):
    m,t=model(monkeypatch);values,kind=profile.edge(m,(-2,183,192),(-2,183,213))
    assert values=={(81,181,2,129):0,(81,181,2,125):0}
    assert kind=='primary_pclkt1_zero_select'
    assert profile.edge(m,(-2,183,191),(-2,183,213)) is None

@pytest.mark.parametrize('mutation',['explicit','mask','site'])
def test_changed_fse_input_or_site_rejects(monkeypatch,mutation):
    m,t=model(monkeypatch)
    if mutation=='explicit':t[5,26]={(2,129):0,(2,125):0}
    elif mutation=='mask':t[4,26].pop((2,129))
    else:m.device.grid[81][181]=17
    with pytest.raises(ValueError):profile.edge(m,(-2,183,192),(-2,183,213))

def test_fixed_copy_and_central_site_are_narrowly_scoped(monkeypatch):
    m,t=model(monkeypatch);m.indegree[0]=2
    with pytest.raises(ValueError):profile.edge(m,(-1,-1,19),(-1,-1,300))
    m.global_choice=lambda *args:(54,89,{(20,58):1})
    with pytest.raises(ValueError):profile.edge(m,(-2,-1,132),(-1,-1,19))
