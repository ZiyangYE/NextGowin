import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json
from ownpr_heterogeneous_timing import HeterogeneousTiming
from ownpr_cell_timing import NativeCellTiming
from ownpr_extended_timing_model import ExtendedTimingModel
from ownpr_mux_architecture import MuxArchitecture


@pytest.fixture(scope='module')
def context():
    d=load_json(ROOT/'results/vexriscv_wide_mapping_search_002/placement/packed.json')
    m=load_json(ROOT/'results/vexriscv_wide_joint_trial_001/full_feedback_repair_001/model.json')
    libs=NativeCellTiming(),ExtendedTimingModel(),MuxArchitecture()
    return d,m,libs


def test_native_profile_proves_only_missing_bypass_clock_ports(context):
    d,m,libs=context;engine=HeterogeneousTiming(d,m,*libs)
    assert len(engine.inactive_clocks)==8
    assert all(x['independent_graph_edges']==1656 for x in engine.inactive_clock_evidence)
    for n,p in engine.inactive_clocks:
        assert d['modules']['top']['cells'][n]['type']=='MULTALU27X18'
        assert all(n+'/'+p not in m[key]['nodes'] for key in ('late','early'))
    # The exemption never creates a timing seed or a zero clock.
    assert m['late']['seeds']==engine.model['late']['seeds']


def test_registered_mode_cannot_reuse_bypass_evidence(context):
    d,m,libs=context;bad=copy.deepcopy(d);n=next(n for n,c in bad['modules']['top']['cells'].items() if c['type']=='MULTALU27X18')
    key=next(k for k in bad['modules']['top']['cells'][n]['parameters'] if k.endswith('_CLK'))
    bad['modules']['top']['cells'][n]['parameters'][key]='CLK0'
    with pytest.raises(ValueError):HeterogeneousTiming(bad,m,*libs)
    engine=HeterogeneousTiming(d,m,*libs)
    with pytest.raises(ValueError,match='primitive'):engine.evaluate(bad)


def test_real_timed_clock_vertex_blocks_inactive_exemption(context):
    d,m,libs=context;engine=HeterogeneousTiming(d,m,*libs);n,p=next(iter(engine.inactive_clocks));bad=copy.deepcopy(m)
    bad['late']['nodes'].append(n+'/'+p);bad['late']['seeds'][n+'/'+p]=[0.,0.]
    with pytest.raises(ValueError,match='Inactive DSP clock is timed'):HeterogeneousTiming(d,bad,*libs)


def test_missing_active_ff_clock_is_still_rejected(context):
    d,m,libs=context;engine=HeterogeneousTiming(d,m,*libs);bad=copy.deepcopy(d)
    name=next(n for n,c in bad['modules']['top']['cells'].items() if c['type'].startswith('DFF'))
    bad['modules']['top']['cells'][name]['connections']['CLK']=[-987654]
    with pytest.raises(ValueError,match='clock-driver'):engine.evaluate(bad)


def test_bypass_exemption_cannot_attach_a_new_live_clock(context):
    d,m,libs=context;engine=HeterogeneousTiming(d,m,*libs);bad=copy.deepcopy(d);name,pin=next(iter(engine.inactive_clocks))
    index=int(pin.split('[')[1].split(']')[0]);live=next(c['connections']['CLK'][0] for c in d['modules']['top']['cells'].values() if c['type'].startswith('DFF'))
    bad['modules']['top']['cells'][name]['connections']['CLK'][index]=live
    with pytest.raises(ValueError,match='inactive DSP clock connection'):engine.evaluate(bad)
