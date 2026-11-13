import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_hold_buffers import input_branches,insert_buffers
from ownpr_image_storage import load_json,read_text
from ownpr_device import Device


@pytest.fixture(scope='module')
def source():
    p=ROOT/'results/vexriscv_lut_replication_003';sta=ROOT/'results/vexriscv_replica_export_003/native'
    d=load_json(p/'packed.json');layout=load_json(p/'placement.json')
    targets,inventory=input_branches(read_text(sta/'probe.tr'),d,layout,load_json(sta/'name_map.json')['instances'],128)
    return d,layout,targets,inventory


def test_complete_real_hold_batch_preserves_every_original_edge_and_boundary(source):
    d,p,targets,inventory=source
    # Two endpoint paths share the same first data branch; buffer it only once.
    assert inventory['supported_branches']==116 and not inventory['skipped'] and len(targets)==116
    before=copy.deepcopy(d);cal=load_json(ROOT/'results/native_device_lut_004/lut_encoding_audit.json')
    new,layout,added=insert_buffers(d,p,Device(),cal,targets,4)
    assert len(added)==464 and d==before
    assert new['modules']['top']['ports']==d['modules']['top']['ports']
    for category in ('fixed_io','hard_clusters','arithmetic_clusters','mux_clusters'):
        assert layout.get(category)==p.get(category)
    assert all(layout['clusters'][n]==v for n,v in p['clusters'].items())
    cells=new['modules']['top']['cells'];redirect={cells[r['instance']]['connections']['F'][0]:cells[r['instance']]['connections']['I0'][0] for r in added}
    def original(b):
        seen=set()
        while b in redirect:
            assert b not in seen;seen.add(b);b=redirect[b]
        return b
    for n,c in d['modules']['top']['cells'].items():
        restored=copy.deepcopy(cells[n]);restored['connections']={pin:[original(b) for b in bs] for pin,bs in restored['connections'].items()}
        assert restored==c
    for row in added:
        cell=cells[row['instance']];assert int(cell['parameters']['INIT'],2)==0xaaaa
    all_sites=[(r['row'],r['column'],r['slot']) for r in layout['clusters'].values()]
    assert len(all_sites)==len(set(all_sites))
    forbidden={(r['row'],r['column']+i) for r in p['hard_clusters'].values() for i in range(3)}
    assert all(tuple(r['site'][:2]) not in forbidden for r in added)


def test_batch_rejects_duplicate_edges_and_output_pins(source):
    d,p,targets,_=source;dev=Device();cal=load_json(ROOT/'results/native_device_lut_004/lut_encoding_audit.json')
    with pytest.raises(ValueError,match='Duplicate'):insert_buffers(d,p,dev,cal,[targets[0]]*2,1)
    with pytest.raises(ValueError,match='receiver'):insert_buffers(d,p,dev,cal,[dict(targets[0],pin='F')],1)


def test_hold_table_accepts_hard_bus_endpoints_and_negative_zero():
    from ownpr_route_eco import violations
    report='3.1.2 Hold Paths Table\n 1 -0.000 own_i_0001/O own_i_0002/DI[3] x\n3.2 Other\n'
    assert violations(report,'Hold')==[(-0.0,'own_i_0001/O','own_i_0002/DI[3]')]


def test_existing_pulse_failure_never_becomes_qualified_or_silently_worse():
    from run_ownpr_hold_closure import exploration_improves
    from run_ownpr_pin_closure import improves
    before=dict(setup_wns_ns=-15.,hold_violations=118,hold_wns_ns=-.925,other_checks_passed=False)
    after=dict(before,hold_violations=8,hold_wns_ns=-.1)
    timing=dict(async_constraints_passed=True,minimum_pulse_width_passed=False,pulse_width_checks=3092,minimum_pulse_width_slack_ns=-.226)
    assert not exploration_improves(before,before,after,timing,timing)
    assert exploration_improves(before,before,after,timing,timing,True)
    assert not improves(before,after)
    assert not exploration_improves(before,before,after,timing,dict(timing,minimum_pulse_width_slack_ns=-.227),True)
    assert not exploration_improves(before,before,after,timing,dict(timing,async_constraints_passed=False),True)


def test_wide_selector_hold_branch_is_buffered_without_breaking_macro():
    from ownpr_mux_macros import validate
    p=ROOT/'results/tmds_wide_ff_place_002';sta=ROOT/'results/tmds_wide_ff_trial_004/export/native'
    design=load_json(p/'packed.json');placed=load_json(p/'placement.json')
    targets,info=input_branches(read_text(sta/'probe.tr'),design,placed,load_json(sta/'name_map.json')['instances'],1)
    assert len(targets)==1 and targets[0]['pin']=='S0' and targets[0]['receiver'] in placed['mux_clusters']
    device=Device();cal=load_json(ROOT/'results/native_device_lut_004/lut_encoding_audit.json')
    new,np,added=insert_buffers(design,placed,device,cal,targets,1)
    assert len(added)==1 and np['mux_clusters']==placed['mux_clusters'] and np['fixed_io']==placed['fixed_io']
    assert new['modules']['top']['ports']==design['modules']['top']['ports']
    assert all(new['modules']['top']['cells'][n]==c for n,c in design['modules']['top']['cells'].items() if c['type'].startswith('DFF'))
    assert len(validate(new,np,device)['wide_ff_pairs'])==6
    with pytest.raises(ValueError,match='receiver'):insert_buffers(design,placed,device,cal,[dict(targets[0],pin='I0')],1)
