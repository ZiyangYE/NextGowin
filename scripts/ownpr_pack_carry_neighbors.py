"""Place critical ordinary LUT neighbors into calibrated carry-CFU spare slots."""
import argparse,copy,json,sys,time,shutil
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from ownpr_critical_chain_move import locations,exclusive_tiles
from ownpr_carry_lut_sharing import calibration


def native_neighbor_ranking(design,ranking,logical,wire_peaks):
    """Include ALU/LUT edges hidden by one-worst-path-per-endpoint reports."""
    cells=design['modules']['top']['cells'];drivers={b:(n,p) for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    rows=list(ranking['ranked_nets']);extra=[];luts={'LUT1','LUT2','LUT3','LUT4'}
    for name,c in cells.items():
        for pin,bits in c['connections'].items():
            if c['port_directions'][pin]!='input' or len(bits)!=1 or bits[0] not in drivers:continue
            src,port=drivers[bits[0]];typ=cells[src]['type'];lut=None
            if typ=='ALU' and port=='SUM' and c['type'] in luts and pin.startswith('I'):lut=name
            elif typ in luts and port=='F' and c['type']=='ALU' and pin in ('I0','I1','I3'):lut=src
            if lut is None:continue
            values=[v['original_setup_slack_ns'] for (n,p),v in logical.items() if n==lut and (p=='F' or p.startswith('I'))]
            net='net_'+str(bits[0])
            if not values or net not in wire_peaks:continue
            extra.append(dict(net=net,driver=dict(instance=src,primitive=typ,pin=port),endpoints=[name+'/'+pin],
                worst_path_slack_ns=min(values),maximum_reported_wire_ns=wire_peaks[net],
                ranking_basis='Native worst-through-LUT slack; wire value is the measured whole-net peak, not necessarily a reported path edge'))
    return dict(ranking,ranked_nets=rows+extra),dict(added_logical_neighbor_edges=len(extra),scope='All supported ALU/LUT logical adjacencies with valid native through-pin feedback')

def propose(design,placed,ranking,priorities,count=8,preferred_slot=6,require_distance_gain=True,independent=False):
    if preferred_slot not in (6,7):raise ValueError('Only calibrated spare slots may be preferred')
    cells=design['modules']['top']['cells'];points=locations(placed);excluded=exclusive_tiles(placed);lut_types={'LUT1','LUT2','LUT3','LUT4'}
    drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs};edges=[]
    for name,c in cells.items():
        for pin,bs in c['connections'].items():
            if c['port_directions'][pin]!='input' or pin in ('CLK','CLKA','CLKB','CIN'):continue
            for bit in bs:
                src=drivers.get(bit)
                if src in points and name in points and src!=name:edges.append((src,name,1.+19.*priorities.get((name,pin),0.)**2))
    options={};occupied={(r['row'],r['column'],r['slot']) for r in placed['clusters'].values()}
    def ordinary(n):
        row=placed['clusters'].get(n)
        return row and cells[n]['type'] in lut_types and not row.get('ff') and (row['row'],row['column']) not in excluded
    for item in ranking['ranked_nets']:
        source=item['driver']['instance']
        for endpoint in item['endpoints']:
            target,pin=endpoint.rsplit('/',1);pair=None
            if source in placed.get('arithmetic_clusters',{}) and ordinary(target):pair=(target,source)
            elif target in placed.get('arithmetic_clusters',{}) and ordinary(source):pair=(source,target)
            if pair is None:continue
            lut,alu=pair;site=placed['arithmetic_clusters'][alu];key=(lut,site['row'],site['column'])
            if key not in options or item['worst_path_slack_ns']<options[key]['slack_ns']:
                options[key]=dict(lut=lut,alu=alu,row=site['row'],column=site['column'],slack_ns=item['worst_path_slack_ns'],wire_ns=item['maximum_reported_wire_ns'])
    if independent:
        scored=[]
        for row in options.values():
            name=row['lut'];position=row['row'],row['column'];incident=[e for e in edges if name in e[:2]]
            def cost(point):
                return sum(w*(abs((point if a==name else points[a])[0]-(point if b==name else points[b])[0])+abs((point if a==name else points[a])[1]-(point if b==name else points[b])[1])) for a,b,w in incident)
            old=cost(points[name]);new=cost(position);slots=[s for s in (preferred_slot,13-preferred_slot) if (*position,s) not in occupied]
            if not slots or require_distance_gain and new>=old:continue
            before=placed['clusters'][name]
            move=dict(row,old_site=[before[k] for k in ('row','column','slot')],new_site=[*position,slots[0]],weighted_distance_before=old,weighted_distance_after=new)
            scored.append(((row['slack_ns'],-(old-new),name,*position),move))
        chosen=[];counts={}
        for _,move in sorted(scored,key=lambda x:x[0]):
            n=move['lut']
            if n not in counts and len(counts)>=count or counts.get(n,0)>=2:continue
            counts[n]=counts.get(n,0)+1;chosen.append(move)
        if not chosen:raise ValueError('No calibrated unoccupied carry neighbor site')
        # No destination is reserved until measured candidates are selected.
        return None,chosen
    layout=copy.deepcopy(placed);chosen=[];remaining=list(options.values());moved=set()
    for _ in range(count):
        scored=[]
        for row in remaining:
            name=row['lut']
            if name in moved:continue
            position=row['row'],row['column'];incident=[e for e in edges if name in e[:2]]
            def cost(point):
                return sum(w*(abs((point if a==name else points[a])[0]-(point if b==name else points[b])[0])+abs((point if a==name else points[a])[1]-(point if b==name else points[b])[1])) for a,b,w in incident)
            old=cost(points[name]);new=cost(position)
            if require_distance_gain and new>=old:continue
            for slot in (6,7):
                if (*position,slot) not in occupied:scored.append(((row['slack_ns'],-(old-new),name,slot!=preferred_slot,slot),row,slot,old,new))
        if not scored:break
        _,row,slot,old,new=min(scored,key=lambda x:x[0]);name=row['lut'];entry=layout['clusters'][name]
        before=[entry['row'],entry['column'],entry['slot']];after=[row['row'],row['column'],slot]
        occupied.remove(tuple(before));occupied.add(tuple(after));entry.update(row=after[0],column=after[1],slot=slot);points[name]=tuple(after[:2]);moved.add(name)
        chosen.append(dict(**row,old_site=before,new_site=after,weighted_distance_before=old,weighted_distance_after=new))
    if not chosen:raise ValueError('No critical carry neighbor benefits under incident-net geometry')
    layout['mixed_carry_lut_calibration']=calibration()['capture_sha256'];layout.update(timing_signoff=False,physical_legality_complete=False);layout.pop('weighted_internal_manhattan',None)
    return layout,chosen

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('base','routes','sta','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--count',type=int,default=8);p.add_argument('--preferred-slot',type=int,choices=(6,7),default=6);a=p.parse_args()
    if not 1<=a.count<=32:p.error('Count must be 1..32')
    from workspace_budget import reserve
    from ownpr_critical_tree_rebuild import verify_feedback
    reserve(ROOT,40_000_000);started=time.monotonic();verify_feedback(a.base,a.routes,a.sta)
    d=load_json(a.base/'packed.json');layout=load_json(a.base/'placement.json');sys.path.insert(0,str(ROOT/'experiments'))
    from analyze_ownpr_critical_wires import analyze
    from ownpr_placement_feedback import load
    priorities,feedback=load(a.sta,d,relative_window_ns=4.);ranking=analyze(a.sta,a.base,a.routes,128);new,changes=propose(d,layout,ranking,priorities,a.count,a.preferred_slot);dev=Device()
    from ownpr_carry_macros import validate as carry
    from ownpr_mux_macros import validate as mux
    from ownpr_ff_configuration import compile_initial
    from ownpr_hard_architecture import HardArchitecture
    audit=carry(d,new,dev);mux(d,new,dev);compile_initial(d,new)
    if new.get('hard_clusters'):HardArchitecture().validate(d,new,dev)
    sites=[(x['row'],x['column'],x['slot']) for x in new['clusters'].values()]
    if len(set(sites))!=len(sites):raise ValueError('Duplicate LUT site')
    a.out.mkdir(parents=True,exist_ok=False);shutil.copyfile(a.base/'packed.json',a.out/'packed.json')
    (a.out/'placement.json').write_text(json.dumps(new,indent=2));(a.out/'changes.json').write_text(json.dumps(changes,indent=2));(a.out/'carry_audit.json').write_text(json.dumps(audit,indent=2))
    sources=[Path(__file__),ROOT/'scripts/ownpr_carry_lut_sharing.py']
    for src in sources:shutil.copyfile(src,a.out/(src.name+'.txt'))
    m=load_json(a.base/'manifest.json');m.update(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),source_netlist_sha256=sha(a.base/'packed.json'),source_placement_sha256=sha(a.base/'placement.json'),source_sta_sha256=sha(a.sta/'probe.tr'),carry_neighbor_moves=changes,carry_audit=audit,placement_feedback=feedback,logical_netlist_byte_identical=True,clock_endpoints_unchanged=True,wall_seconds=time.monotonic()-started,timing_signoff=False,producer_source_sha256={f.name:sha(a.out/(f.name+'.txt')) for f in sources})
    m.pop('rollback_luts',None);m.pop('source_failed_placement_sha256',None);m['preferred_spare_slot']=a.preferred_slot
    (a.out/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps(changes,indent=2))

if __name__=='__main__':main()
