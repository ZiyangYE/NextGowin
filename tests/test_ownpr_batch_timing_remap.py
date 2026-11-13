import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_batch_timing_remap import select,apply_batch


def option(parent,root,paths,wire=2.,growth=0):return dict(parent=parent,root=root,witness_paths=paths,reported_wire_ns=wire,lut_growth=growth,kind='ordinary')


def test_joint_choice_covers_competing_paths_instead_of_repeatedly_optimizing_one():
    paths=[dict(slack_ns=.01),dict(slack_ns=.02),dict(slack_ns=.3),dict(slack_ns=2.)]
    options=[option('a','b',[0],4),option('c','d',[0],3),option('e','f',[1],2),option('g','h',[2],1),option('i','j',[3],20)]
    before=copy.deepcopy(options);selected,report=select(options,paths,count=3,window=.5)
    assert [r['root'] for r in selected]==['b','f','h'] and report['uncovered_paths']==[]
    assert report['covered_paths']==[0,1,2] and options==before


def test_conflicts_and_area_budget_prevent_incompatible_batches():
    options=[option('a','b',[0],4,3),option('b','c',[1],4),option('d','e',[1],3,3),option('f','g',[1],2)]
    selected,report=select(options,[dict(slack_ns=0),dict(slack_ns=.01)],count=3,max_growth=3)
    assert [r['root'] for r in selected]==['b','g'] and report['lut_growth']==3
    with pytest.raises(ValueError,match='Overlapping'):apply_batch(None,None,None,[options[0],options[1]])


def test_batch_rejects_invalid_limits():
    with pytest.raises(ValueError):select([],[],count=0)
    with pytest.raises(ValueError):select([],[],window=float('nan'))


def test_real_mixed_cpu_batch_matches_proved_netlist_and_keeps_all_state_boundaries():
    from ownpr_device import Device
    from ownpr_image_storage import load_json
    from ownpr_ff_configuration import FF_CONTROLS
    base=ROOT/'results/vexriscv_timing_cut_place_001';out=ROOT/'results/vexriscv_batch_cut_place_001'
    d=load_json(base/'packed.json');p=load_json(base/'placement.json');m=load_json(out/'manifest.json')
    after,placed,changes=apply_batch(d,p,Device(),[r['candidate'] for r in m['changes']])
    assert after==load_json(out/'packed.json') and placed==load_json(out/'placement.json')
    assert sum(r['transform']['lut_growth'] for r in changes)==5
    assert all(v==placed[k] for k,v in p.items() if k in ('fixed_io','hard_clusters','arithmetic_clusters'))
    protected=set(FF_CONTROLS)|{'ALU','DPX9B','SDPX9B','MULTALU27X18','IBUF','OBUF'}
    assert all(after['modules']['top']['cells'][n]==c for n,c in d['modules']['top']['cells'].items() if c['type'] in protected)
    assert all(row['cec']['equivalent'] and row['observed_hard_inputs_and_ports']==8584 for row in m['proof'])
