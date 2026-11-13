"""Swap two ordinary LUT/FF pairs, preserving physical clock sink locations."""
import argparse,copy,json,shutil
from pathlib import Path
from ownpr_device import Device,ROOT
from ownpr_place import sha
from ownpr_mux_macros import validate
from ownpr_ff_configuration import FF_CONTROLS,compile_initial
from workspace_budget import reserve


def swap(design,placed,device,first,second):
    if first==second:raise ValueError('Cluster swap needs two different clusters')
    validate(design,placed,device);cells=design['modules']['top']['cells'];clusters=placed['clusters']
    excluded={(x['row'],x['column']) for kind in ('arithmetic_clusters','mux_clusters') for x in placed.get(kind,{}).values()}
    lut_file=ROOT/'results/native_device_lut_004/lut_encoding_audit.json';cls_file=ROOT/'results/native_cls_modes_002/cls_mode_audit.json';capture_file=cls_file.with_name('cls_mode_sweep.json')
    lut=json.loads(lut_file.read_text());cls=json.loads(cls_file.read_text());capture=json.loads(capture_file.read_text())
    if (lut['status']!='verified_sample_lut_init_mapping' or not all(r['match'] for r in lut['checks']) or
        cls['status']!='verified_sample_cls_predicates' or not all(r['match'] for r in cls['checks']) or
        any(d['source_fse_sha256']!=device.fse.sha256 for d in (lut,cls,capture))):raise ValueError('Cluster calibration differs from native device')
    covered={(g['tile_type'],g['group']) for g in capture['groups']}
    allowed={t for t in lut['tile_types'] if all((t,g) in covered for g in range(4))}
    def point(x):return x['row'],x['column'],x['slot']
    members=[]
    for name in (first,second):
        c=clusters.get(name)
        if not c or not c.get('ff') or not c.get('lut') or c['lut']!=name:raise ValueError('Swap requires an ordinary paired LUT/FF cluster')
        r,col,slot=point(c)
        if not 0<=r<device.rows or not 0<=col<device.cols or device.grid[r][col] not in allowed or slot not in (0,2,4):raise ValueError('Uncalibrated paired cluster site')
        if (r,col) in excluded:raise ValueError('Cluster swap overlaps an exclusive carry/wide macro')
        if cells[name]['type'] not in ('LUT1','LUT2','LUT3','LUT4') or cells[c['ff']]['type'] not in FF_CONTROLS:raise ValueError('Unsupported paired primitive')
        if cells[name]['connections']['F']!=cells[c['ff']]['connections']['D']:raise ValueError('Pair is not connected by its dedicated D link')
        members.append(c)
    if cells[members[0]['ff']]['connections']['CLK']!=cells[members[1]['ff']]['connections']['CLK']:raise ValueError('Cannot swap different clock domains')
    def capacity(table):
        occupied=set();controls=set()
        for row in table.values():
            p=point(row)
            if p in occupied:raise ValueError('Overlapping cluster slots')
            occupied.add(p)
            if row.get('ff'):
                group=(p[0],p[1],p[2]//2)
                if group in controls:raise ValueError('Uncalibrated shared FF control group')
                controls.add(group)
    capacity(clusters);before={point(c) for c in clusters.values() if c.get('ff')};result=copy.deepcopy(placed)
    a,b=point(members[0]),point(members[1])
    for name,p in ((first,b),(second,a)):result['clusters'][name].update(row=p[0],column=p[1],slot=p[2])
    capacity(result['clusters']);validate(design,result,device);compile_initial(design,result)
    if before!={point(c) for c in result['clusters'].values() if c.get('ff')}:raise ValueError('Swap changed physical clock sink positions')
    result.pop('weighted_internal_manhattan',None)
    result.update(capacity_legal=True,physical_legality_complete=False,timing_signoff=False,clock_placement_optimized=False)
    change=dict(first=first,second=second,first_ff=members[0]['ff'],second_ff=members[1]['ff'],first_old_site=list(a),second_old_site=list(b),
        logical_netlist_byte_identical=True,clock_sink_sites_preserved=True,fixed_io_preserved=True,
        calibrated_tile_types=sorted(allowed),calibration_sha256={str(p):sha(p) for p in (lut_file,cls_file,capture_file)})
    result['cluster_swap']=change
    return result,change


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',type=Path,required=True);p.add_argument('--pair',nargs=2,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,10_000_000);design=json.loads((a.base/'packed.json').read_text());old=json.loads((a.base/'placement.json').read_text())
    placed,change=swap(design,old,Device(),*a.pair);a.out.mkdir(exist_ok=False);shutil.copyfile(a.base/'packed.json',a.out/'packed.json')
    (a.out/'placement.json').write_text(json.dumps(placed,indent=2));meta=json.loads((a.base/'manifest.json').read_text())
    meta.update(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),source_placement_sha256=sha(a.base/'placement.json'),
        cluster_swap=change,physical_legality_complete=False,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(change,indent=2))
