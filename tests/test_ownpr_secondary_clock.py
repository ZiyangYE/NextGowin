import json,sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_clock_diagnostics import sample_paths
from ownpr_clock_configuration import ClockChoices
from ownpr_clock_graph import ClockGraph
from ownpr_device import Device
from audit_native_clock_encoding import audit
from run_ownpr_pin_closure import metric,improves
from ownpr_lut_pin_eco import setup_pin_targets
from ownpr_pin_slack import downstream_budgets


def read(p):return json.loads(p.read_text())


def test_cyclic_clock_sampling_is_bounded_and_simple():
    g=SimpleNamespace(by_coordinate={(i,0,0):i for i in range(5)},adj=defaultdict(list,{0:[1,2],1:[2,3],2:[1,4],3:[4]}))
    paths,info=sample_paths(g,(0,0,0),(4,0,0),max_nodes=5)
    assert set(paths)=={(0,1,3,4),(0,1,2,4),(0,2,4),(0,2,1,3,4)}
    assert all(len(p)==len(set(p)) for p in paths) and info['enumeration_complete']
    paths,info=sample_paths(g,(0,0,0),(4,0,0),max_nodes=3)
    assert paths==[(0,2,4)] and info['enumeration_scope']=='simple paths within max_nodes'
    paths,info=sample_paths(g,(0,0,0),(4,0,0),max_states=1)
    assert not paths and not info['enumeration_complete'] and info['pending_states']
    paths,info=sample_paths(g,(0,0,0),(4,0,0),max_paths=1)
    assert len(paths)==1 and not info['enumeration_complete']
    with pytest.raises(ValueError,match='positive'):sample_paths(g,(0,0,0),(4,0,0),max_nodes=0)


def test_secondary_tree_matches_independent_native_encoding_and_disable_masks():
    device=Device();graph=ClockGraph(ROOT/'results/native_clock_inventory_005');choices=ClockChoices(device,graph)
    folder=ROOT/'results/native_uart_secondary_clock_encoding_001'
    r=audit(folder,folder/'clock_tree.json',choices=choices)
    assert r['status']=='clock_candidate_bits_matched' and r['full_payload_restored']
    assert r['actual_changes']==r['predicted_changes']>0 and not r['full_clock_legality_verified']
    bits,info=choices.requirements(read(folder/'clock_tree.json'))
    mask=choices.disabled_global_mux(91,17)
    assert mask and all(bits[b]==v==0 for b,v in mask.items())
    assert info['edge_kinds']['secondary_source_mux']==1
    # A competing nominal-primary selection must disagree with secondary mode.
    r,c,primary=choices.global_choice(91,132,17)
    assert any(v==1 and bits[r,c,y,x]==0 for (y,x),v in primary.items())
    with pytest.raises(ValueError,match='Uncalibrated'):choices.disabled_global_mux(91,25)


def test_faster_io_clock_is_rejected_when_fullio_ce_setup_regresses():
    base=ROOT/'results/ownpr_uart_ioreg_pin_closure_002/round_003'
    old=metric(base/'export');new=metric(ROOT/'results/native_ownpr_uart_secondary_export_001')
    assert old['reported_fmax_mhz']==179.068 and new['reported_fmax_mhz']==169.943
    assert not improves(old,new) and new['hold_violations']==0 and new['other_checks_passed']
    routes=ROOT/'results/ownpr_uart_secondary_clock_routes_001'
    for name in ('routes.json','nets.json','all_nets.json'):
        assert (base/'eco'/name).read_bytes()==(routes/name).read_bytes()
    decision=read(ROOT/'results/ownpr_uart_secondary_comparison_001/summary.json')
    assert decision['candidates'][0]['decision']=='rejected_global_timing'
    assert decision['fixed_io_and_source_sdc_preserved'] and not decision['vendor_routing_executed']


def test_pin_targets_skip_output_only_worst_path_and_remain_bounded():
    folder=ROOT/'results/ownpr_uart_ioreg_pin_closure_002/round_003/export'
    report=(folder/'probe.tr').read_text()
    targets,paths=setup_pin_targets(report,max_paths=2,max_pins=3)
    assert targets and len(targets)<=3 and 0<len(paths)<=2 and paths[0]['path']>1
    assert all(r['slack_ns']<0 for r in paths)


def test_mux_cone_hold_budget_includes_all_ff_and_output_branches():
    def cell(typ,connections,outs):return dict(type=typ,connections={p:[b] for p,b in connections.items()},port_directions={p:'output' if p in outs else 'input' for p in connections})
    cells={'mux':cell('MUX2_LUT6',{'I0':1,'I1':2,'S0':3,'O':4},['O']),
           'reg':cell('DFFCE',{'D':4,'CE':5,'CLK':6,'CLEAR':7,'Q':8},['Q']),
           'pad':cell('OBUF',{'I':4,'O':9},['O'])}
    mapping={'instances':{'mux':'own_i_0','reg':'own_i_1','pad':'own_i_2'}}
    report='3.1.2 Hold Paths Table\n 1 0.045 own_i_0/O own_i_1/D x\n 2 0.020 own_i_0/O own_i_2/O x\n3.1.3 Next\n3.1.4 Removal Paths Table\n 1 0.100 own_i_0/O own_i_1/CLEAR x\n3.2 Next\n'
    design={'modules':{'top':{'cells':cells}}}
    assert downstream_budgets(design,mapping,report)('mux','S0')==.020
    with pytest.raises(ValueError,match='uncovered'):downstream_budgets(design,mapping,report.replace('own_i_2/O','own_i_9/O'))('mux','I0')
    with pytest.raises(ValueError,match='clock'):downstream_budgets(design,mapping,report)('reg','CLK')


def test_native_export_entry_separates_handoff_success_from_timing_acceptance():
    folder=ROOT/'results/ownpr_uart_ce_wide_handoff_002';m=read(folder/'manifest.json')
    assert m['status']=='verified_handoff_complete' and m['wall_seconds']<m['budget_seconds']<=3600
    assert [s['stage'] for s in m['stages']]==['wire','joint','configuration','final_sta_encoder']
    assert all(s['status']=='passed' for s in m['stages'])
    assert m['sta_completed'] and m['bitstream_generated'] and not m['native_constraints_satisfied']
    assert not m['vendor_placement_executed'] and not m['vendor_routing_executed'] and not m['device_programmed']
    assert not m['full_bitstream_verified'] and m['metrics']['hold_violations']==0
    from ownpr_place import sha
    assert all(sha(folder/'native'/n)==v for n,v in m['output_sha256'].items())
    assert all(sha(Path(n))==v for n,v in m['source_sha256'].items())
    wide=read(ROOT/'results/ownpr_uart_ce_wide_cone_001/manifest.json')
    assert wide['transform']['cut_width']==6 and all(p['cec']['equivalent'] for p in wide['proof'])


def test_rejected_last_pin_candidate_stops_without_requesting_nonexistent_rank(tmp_path,monkeypatch):
    import run_ownpr_pin_closure as runner
    # This test stubs every subprocess and operates only on its temporary tree.
    monkeypatch.setattr(runner,'ROOT',tmp_path)
    sta=tmp_path/'sta';sta.mkdir();(sta/'manifest.json').write_text(json.dumps({'cst_sha256':'x','source_sdc_sha256':'x'}))
    out=tmp_path/'out'
    args=['prog']
    for name in ('placement','routes','clock-capture','cst','sdc','io-reference'):
        args.extend(['--'+name,str(tmp_path/name)])
    args.extend(['--sta',str(sta),'--out',str(out),'--rounds','8'])
    monkeypatch.setattr(sys,'argv',args);monkeypatch.setattr(runner,'sha',lambda p:'x')
    baseline=dict(setup_wns_ns=-.1,setup_violations=1,total_negative_setup_slack_ns=-.1,
        hold_violations=0,hold_wns_ns=.01,other_checks_passed=True,native_constraints_satisfied=False)
    monkeypatch.setattr(runner,'metric',lambda p:baseline)
    calls=[]
    class Process:
        def __init__(self,command,**kwargs):
            calls.append(command)
            if Path(command[1]).name=='ownpr_lut_pin_eco.py':
                eco=Path(command[command.index('--out')+1]);eco.mkdir()
                (eco/'manifest.json').write_text(json.dumps({'pin_permutation':{'legal_candidate_count':1}}))
        def wait(self,timeout):return 0
    monkeypatch.setattr(runner.subprocess,'Popen',Process)
    runner.main();r=read(out/'manifest.json')
    assert r['status']=='stopped_without_further_global_improvement' and len(r['rounds'])==1
    assert len(calls)==5 and r['rounds'][0]['status']=='rejected_global_timing'
