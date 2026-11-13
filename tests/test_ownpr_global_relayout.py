import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from ownpr_device import Device

def test_real_global_candidate_preserves_cells_io_and_macro_shapes():
    base=ROOT/'results/vexriscv_control_batch_place_002';candidate=ROOT/'results/vexriscv_global_relayout_001'
    assert sha(base/'packed.json')==sha(candidate/'packed.json')
    old=load_json(base/'placement.json');new=load_json(candidate/'placement.json');design=load_json(candidate/'packed.json')
    assert old['fixed_io']==new['fixed_io'] and old['hard_clusters']==new['hard_clusters']
    assert set(old['clusters'])==set(new['clusters']) and set(old['arithmetic_clusters'])==set(new['arithmetic_clusters'])
    for name,row in old['clusters'].items():
        assert {k:v for k,v in row.items() if k not in ('row','column','slot')}=={k:v for k,v in new['clusters'][name].items() if k not in ('row','column','slot')}
    from ownpr_mux_macros import validate as validate_wide
    from ownpr_carry_macros import validate as validate_carry
    from ownpr_ff_configuration import compile_initial
    from ownpr_hard_architecture import HardArchitecture
    from ownpr_wide_ff_packing import pairs
    device=Device();validate_wide(design,new,device);validate_carry(design,new,device);compile_initial(design,new);HardArchitecture().validate(design,new,device)
    before=pairs(design,old,device);after=pairs(design,new,device)
    assert len(before)==len(after)==1 and before[0]['profile']==after[0]['profile'] and before[0]['ff']==after[0]['ff']
    locations=[(r['row'],r['column'],r['slot']) for r in new['clusters'].values()]
    assert len(locations)==len(set(locations))
    changed=sum(any(row[k]!=new['clusters'][name][k] for k in ('row','column','slot')) for name,row in old['clusters'].items())
    assert changed>4000

def test_global_unit_graph_keeps_wide_ff_and_carry_together():
    from ownpr_global_relayout import units
    base=ROOT/'results/vexriscv_control_batch_place_002';d=load_json(base/'packed.json');p=load_json(base/'placement.json');rows,owner=units(d,p)
    for row in p['clusters'].values():
        if row.get('wide_ff_root'):assert owner[row['ff']]==owner[row['wide_ff_root']]==owner[row['lut']]
    for name,row in p['arithmetic_clusters'].items():assert owner[name]=='carry:'+row['chain']
    assert all(rows[owner[n]]['kind']=='fixed' for n in p['fixed_io'])


def test_fixed_ioreg_boundaries_are_present_in_all_four_unit_graphs():
    from ownpr_global_relayout import units,edges
    packet=load_json(ROOT/'results/joint_wide_move_map_trial_012/all_designs_reported_fmax_checkpoint.json')
    bases=[ROOT/r['selected']['placement'] for r in packet['designs']]
    bases.append(ROOT/'results/endpoint_class_cooptimization_trial_019/int2float_endpoint_placement_001')
    seen=0
    for base in bases:
        d=load_json(base/'packed.json');p=load_json(base/'placement.json');rows,owner=units(d,p)
        assert sum(len(v['members']) for v in rows.values())==len(owner)
        edge_rows=edges(d,owner,{})
        for ff,group in p.get('ioreg_clusters',{}).items():
            seen+=1;key=owner[ff];io=owner[group['iobuf']]
            assert key!=io and rows[key]['kind']=='fixed'
            assert rows[key]['point']==tuple(v-1 for v in group['site'][:2])
            # The OBUF routing exit may sit one tile inward from IOLOGIC.
            assert rows[io]['point']==tuple(v-1 for v in p['fixed_io'][group['iobuf']]['native_ports']['out'][:2])
            assert any(b==key and a!=io for a,b,w in edge_rows)
            assert any(a==key and b==io for a,b,w in edge_rows)
    assert seen>=16


def test_ioreg_global_candidate_preserves_all_state_and_fixed_sites():
    base=ROOT/'results/endpoint_class_cooptimization_trial_019/int2float_endpoint_placement_001'
    candidate=ROOT/'results/clock_cone_joint_trial_020/int2float_global_001'
    assert sha(base/'packed.json')==sha(candidate/'packed.json')
    old=load_json(base/'placement.json');new=load_json(candidate/'placement.json')
    assert old['fixed_io']==new['fixed_io'] and old['ioreg_clusters']==new['ioreg_clusters']
    assert set(old['clusters'])==set(new['clusters'])
    assert all(row['ff']==new['clusters'][name]['ff'] for name,row in old['clusters'].items())
    assert any(row['ff'] and (row['row'],row['column'])!=(new['clusters'][name]['row'],new['clusters'][name]['column']) for name,row in old['clusters'].items())
