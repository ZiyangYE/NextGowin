"""Check exact-copy operator on every checkpoint; no performance inference."""
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from ownpr_critical_cover import partition_copy
from ownpr_shared_dag import connectivity
from ownpr_critical_chain_move import exclusive_tiles,locations
from ownpr_state_safety import protected_cells
from ownpr_lut_graph_feedback import LUTS
from ownpr_device import Device
from native_boundary_cec import prove
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('checkpoint','budget','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,30000000);a.out.mkdir(exist_ok=False);budget=load_json(a.budget);device=Device();cases=[];inputs={str(a.checkpoint.resolve()):sha(a.checkpoint),str(Path(__file__).resolve()):sha(Path(__file__))}
    for row in load_json(a.checkpoint)['designs']:
        if time.time()+100>budget['deadline_epoch']:raise TimeoutError('Original attempt deadline')
        for path,h in row['selected_source_sha256'].items():
            if sha(ROOT/path)!=h:raise ValueError('Changed checkpoint')
        placement=ROOT/row['selected']['placement'];d=load_json(placement/'packed.json');l=load_json(placement/'placement.json');cells=d['modules']['top']['cells'];_,loads=connectivity(d);bad=protected_cells(d);excluded=exclusive_tiles(l);points=locations(l)
        occupied={(r['row'],r['column'],r['slot']) for r in l['clusters'].values()};choice=None
        for n,c in sorted(cells.items()):
            if c['type'] not in LUTS or n in bad or points[n] in excluded:continue
            consumers=loads[c['connections']['F'][0]]
            safe=[r for r in consumers if cells[r[0]]['type'] in LUTS and r[0] not in bad]
            if len(consumers)<2 or not safe:continue
            center=points[safe[0][0]]
            sites=[(r,c,s) for r in range(max(0,center[0]-2),min(device.rows,center[0]+3)) for c in range(max(0,center[1]-2),min(device.cols,center[1]+3)) if device.grid[r][c]==17 and (r,c) not in excluded for s in range(8) if (r,c,s) not in occupied]
            if sites:choice=n,[safe[0]],sites[0];break
        record=dict(design=row['design'],physical_performance_tested=False,critical_cover_selection_tested=False,source_packed_sha256=sha(placement/'packed.json'));cases.append(record)
        if choice:
            folder=a.out/row['design'];folder.mkdir();nd,nl,change=partition_copy(d,l,*choice,'guard');proof=prove(d,nd,folder/'cec',75)
            if not proof or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Exact-copy CEC failed')
            (folder/'candidate.json').write_text(json.dumps(nd));(folder/'placement.json').write_text(json.dumps(nl))
            record.update(status='exact_copy_operator_cec_passed',change=change,proof=proof,candidate_sha256=sha(folder/'candidate.json'),full_source_hard_state_equivalence_proved=False)
        else:record['status']='no_eligible_copy_unchanged'
        inputs.update({str((placement/n).resolve()):sha(placement/n) for n in ('packed.json','placement.json')})
        (a.out/'manifest.json').write_text(json.dumps(dict(status='checking',cases=cases),indent=2))
    result=dict(status='cross_design_copy_operator_checked_not_performance',cases=cases,source_sha256=inputs,original_budget=budget,within_original_deadline=time.time()<budget['deadline_epoch'],new_algorithm_performance_generalization_proven=False)
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print([(r['design'],r['status']) for r in cases])


if __name__=='__main__':main()
