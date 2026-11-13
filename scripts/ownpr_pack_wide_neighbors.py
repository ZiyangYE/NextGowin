"""Use measured critical edges to propose ordinary LUT/wide-CFU co-packing.

Incident span ranks candidates; actual loaded routing and full STA decide.
No logical, FF, hard-resource, I/O, or clock changes are permitted.
"""
import argparse,copy,json,sys,time,shutil
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha,copy_original
from ownpr_critical_chain_move import locations,exclusive_tiles,apply as validate
from ownpr_mux_macros import tree
from ownpr_wide_lut_sharing import calibration


def propose(design,layout,ranking,priorities,level=6,count=1):
    if level not in (5,6) or not 1<=count<=4:raise ValueError('Unsupported wide-neighbor scope')
    cells=design['modules']['top']['cells'];points=locations(layout);excluded=exclusive_tiles(layout);owners={};macros={}
    for m in layout.get('wide_macros',[]):
        spec=tree(design,m['root'],m['root_slot'])
        if spec['level']!=level or m['root_slot']!={5:0,6:1}[level]:continue
        if any(v.get('ff') for v in layout['clusters'].values() if (v['row'],v['column'])==(m['row'],m['column'])):continue
        macros[m['root']]=m
        for n in set(spec['leaves'])|set(spec['muxes']):owners[n]=m['root']
    def ordinary(n):
        p=layout['clusters'].get(n)
        return p and cells[n]['type'] in ('LUT1','LUT2','LUT3','LUT4') and not p.get('ff') and (p['row'],p['column']) not in excluded
    drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs};edges=[]
    for n,c in cells.items():
        for p,bs in c['connections'].items():
            if c['port_directions'][p]!='input' or p in ('CLK','CLKA','CLKB','CIN'):continue
            for b in bs:
                d=drivers.get(b)
                if d in points and n in points and d!=n:edges.append((d,n,1.+19.*priorities.get((n,p),0.)**2))
    options={}
    for item in ranking['ranked_nets']:
        src=item['driver']['instance']
        for endpoint in item['endpoints']:
            dst=endpoint.rsplit('/',1)[0];pair=None
            if src in owners and ordinary(dst):pair=(dst,owners[src])
            elif dst in owners and ordinary(src):pair=(src,owners[dst])
            if pair is None:continue
            row=dict(lut=pair[0],root=pair[1],worst_slack_ns=item['worst_path_slack_ns'],reported_wire_ns=item['maximum_reported_wire_ns'])
            if pair not in options or row['worst_slack_ns']<options[pair]['worst_slack_ns']:options[pair]=row
    placed=copy.deepcopy(layout);chosen=[];moved=set();occupied={(p['row'],p['column'],p['slot']) for p in placed['clusters'].values()}
    for _ in range(count):
        scored=[]
        for row in options.values():
            name=row['lut'];m=macros[row['root']];dest=m['row'],m['column']
            if name in moved:continue
            incident=[e for e in edges if name in e[:2]]
            def cost(point):return sum(w*(abs((point if a==name else points[a])[0]-(point if b==name else points[b])[0])+abs((point if a==name else points[a])[1]-(point if b==name else points[b])[1])) for a,b,w in incident)
            before,after=cost(points[name]),cost(dest)
            if after>=before:continue
            for slot in range(1<<(level-4),8):
                if (*dest,slot) not in occupied:scored.append(((row['worst_slack_ns'],-row['reported_wire_ns'],-(before-after),name,slot),row,slot,before,after))
        if not scored:break
        _,row,slot,before,after=min(scored,key=lambda x:x[0]);name=row['lut'];m=macros[row['root']];p=placed['clusters'][name];old=[p[k] for k in ('row','column','slot')];new=[m['row'],m['column'],slot]
        occupied.remove(tuple(old));occupied.add(tuple(new));p.update(row=new[0],column=new[1],slot=slot);points[name]=tuple(new[:2]);moved.add(name)
        next(m for m in placed['wide_macros'] if m['root']==row['root'])['ordinary_sharing']=True
        chosen.append(dict(row,old_site=old,new_site=new,weighted_span_before=before,weighted_span_after=after))
    if not chosen:raise ValueError('No reported wide neighbor has a calibrated free slot and favorable incident span')
    placed['mixed_wide_lut_calibration']=calibration()['admission_sha256'];return placed,chosen


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('base','routes','sta','pin_feedback','out'):p.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    p.add_argument('--level',type=int,choices=(5,6),default=6);p.add_argument('--count',type=int,default=1);a=p.parse_args()
    from workspace_budget import reserve
    from ownpr_critical_tree_rebuild import verify_feedback
    from ownpr_pin_feedback import load,criticalities
    sys.path.insert(0,str(ROOT/'experiments'));from analyze_ownpr_critical_wires import analyze
    reserve(ROOT,100000000);start=time.monotonic();verify_feedback(a.base,a.routes,a.sta);budgets,info=load(a.pin_feedback,a.sta)
    design=load_json(a.base/'packed.json');original=load_json(a.base/'placement.json');ranking=analyze(a.sta,a.base,a.routes,512)
    placed,chosen=propose(design,original,ranking,criticalities(budgets),a.level,a.count);placed=validate(design,placed,Device(),dict(sites={}))
    if placed['fixed_io']!=original['fixed_io'] or placed.get('hard_clusters')!=original.get('hard_clusters') or any(placed['clusters'][n]!=r for n,r in original['clusters'].items() if r.get('ff')):raise ValueError('Fixed state/hard/IO moved')
    a.out.mkdir(exist_ok=False);copy_original(a.base/'packed.json',a.out/'packed.json');(a.out/'placement.json').write_text(json.dumps(placed,indent=2))
    if sha(a.out/'packed.json')!=sha(a.base/'packed.json'):raise ValueError('Logical bytes changed')
    sources=[Path(__file__),ROOT/'scripts/ownpr_wide_lut_sharing.py',ROOT/'scripts/ownpr_mux_macros.py',ROOT/'scripts/ownpr_mux_configuration.py']
    for src in sources:shutil.copyfile(src,a.out/(src.name+'.txt'))
    result=dict(status='placed',selected=chosen,level=a.level,logical_netlist_byte_identical=True,state_locations_unchanged=True,fixed_io_changed=False,clock_replan_required=False,interface_cycles_changed=False,
        netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),pin_feedback=info,wall_seconds=time.monotonic()-start,source_sha256={s.name:sha(a.out/(s.name+'.txt')) for s in sources},
        vendor_pnr_executed=False,timing_signoff=False,ranking_scope='Measured old wire priority and geometric incident cost, not predicted loaded timing')
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))


if __name__=='__main__':main()
