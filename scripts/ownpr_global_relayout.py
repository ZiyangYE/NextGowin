"""Timing-weighted global relaxation with explicit density and macro legalization.

Preserves the checked logical packing, including wide-root/FF associations.
Moves ordinary LUT/FF units, whole carry chains and whole wide macros together.
Hard sites and package I/O are fixed. Wirelength/density are optimization
surrogates; a fresh clock/data route and complete STA decide acceptance.
"""
import argparse,copy,json,math,shutil,time,re
from collections import defaultdict
from pathlib import Path
import numpy as np
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha,read_text
from ownpr_placement_feedback import load as pin_weights
from workspace_budget import reserve

def units(design,placed):
    rows={};owner={};wide_tiles={};shared_owner={}
    if placed.get('shared_ff_calibration'):
        from ownpr_shared_ff_packing import compile_sites
        _,info=compile_sites(design,placed,Device())
        for group in info['records']:
            key='shared:'+group['luts'][0]
            rows[key]=dict(kind='shared',point=tuple(group['tile']),area=1/3,width=1,clusters=group['luts'],members=[])
            shared_owner.update({n:key for n in group['luts']})
    for macro in placed.get('wide_macros',[]):
        key='wide:'+macro['root'];point=(macro['row'],macro['column']);wide_tiles[point]=key
        rows[key]=dict(kind='wide',point=point,area=1.,width=1,root=macro['root'],members=[])
    for name,row in placed['clusters'].items():
        key=wide_tiles.get((row['row'],row['column']),shared_owner.get(name,'logic:'+name))
        if key not in rows:rows[key]=dict(kind='logic',point=(row['row'],row['column']),area=1/3 if row.get('ff') else 1/8,width=1,cluster=name,members=[])
        for member in (name,row.get('ff')):
            if member:rows[key]['members'].append(member);owner[member]=key
    for name,row in placed.get('mux_clusters',{}).items():
        key=wide_tiles[row['row'],row['column']];owner[name]=key;rows[key]['members'].append(name)
    chains=defaultdict(list)
    for name,row in placed.get('arithmetic_clusters',{}).items():chains[row['chain']].append((name,row))
    for chain,items in chains.items():
        rs={v['row'] for _,v in items};left=min(v['column'] for _,v in items);right=max(v['column'] for _,v in items)
        if len(rs)!=1:raise ValueError('Carry chain is not a horizontal macro')
        key='carry:'+chain;rows[key]=dict(kind='carry',point=(next(iter(rs)),(left+right)/2),area=right-left+1.,width=right-left+1,left=left,members=[n for n,_ in items])
        for name,_ in items:owner[name]=key
    for name,row in placed.get('hard_clusters',{}).items():
        key='hard:'+name;rows[key]=dict(kind='fixed',point=(row['row'],row['column']+1),area=0.,members=[name]);owner[name]=key
    for name,row in placed['fixed_io'].items():
        r,c,_=row['native_ports']['out'];key='io:'+name;rows[key]=dict(kind='fixed',point=(r-1,c-1),area=0.,members=[name]);owner[name]=key
    # I/O FFs are fixed physical endpoints, distinct from their OBUF cells.
    # Include their data/control edges when relaxing the whole upstream graph.
    if placed.get('ioreg_clusters'):
        from ownpr_ioreg_macros import validate as validate_ioreg
        validate_ioreg(design,placed,Device())
    for name,row in placed.get('ioreg_clusters',{}).items():
        if name in owner:raise ValueError('I/O FF has duplicate physical ownership')
        r,c,_=row['site'];key='ioreg:'+name
        rows[key]=dict(kind='fixed',point=(r-1,c-1),area=0.,members=[name]);owner[name]=key
    expected={n for n,c in design['modules']['top']['cells'].items() if c['type'] not in ('GND','VCC','GSR')}
    if set(owner)!=expected:raise ValueError('Global unit inventory omits physical cells')
    return rows,owner

def path_demands(folder,design,window=4.):
    """Accumulate complete near-critical path demand without fanout dilution."""
    from project_timing import section
    reverse={v:k for k,v in load_json(folder/'name_map.json')['instances'].items()};cells=design['modules']['top']['cells']
    text=section(read_text(folder/'probe.tr'),'3.3.1 Setup Analysis Report');paths=[]
    for block in re.split(r'^\s*Path\d+\s*$',text,flags=re.M)[1:]:
        slack=re.search(r'^Slack\s*:\s*([-\d.]+)',block,re.M);data=re.search(r'Data Arrival Path:\n(.*?)(?=\nData Required Path:)',block,re.S)
        if not slack or not data:raise ValueError('Incomplete setup path demand witness')
        sinks=set()
        for alias,pin in re.findall(r'^\s*[-\d.]+\s+[-\d.]+\s+tNET\s+\w+\s+\d+\s+\S+\s+(own_i_\d+)/(\w+(?:\[\d+\])?)\s*$',data[1],re.M):
            name=reverse[alias];port=pin.split('[',1)[0]
            if cells[name]['type']=='OBUF' and port=='O':port='I'
            if port not in ('CLK','CLKA','CLKB','CIN') and cells[name]['port_directions'].get(port)=='input':sinks.add((name,port))
        paths.append((float(slack[1]),sinks))
    if not paths:raise ValueError('No complete setup paths')
    worst=min(s for s,_ in paths);demand=defaultdict(float);used=0
    for slack,sinks in paths:
        if slack>worst+window:continue
        used+=1;weight=max(.05,1.-(slack-worst)/window)**2
        for sink in sinks:demand[sink]+=weight
    scale=max(demand.values(),default=0.)
    if not scale:raise ValueError('No supported near-critical path wires')
    return {k:v/scale for k,v in demand.items()},dict(paths=used,reported_paths=len(paths),window_ns=window,worst_slack_ns=worst,weighted_pins=len(demand),source_report_sha256=sha(folder/'probe.tr'))


def edges(design,owner,priorities,path_weights=None,path_gain=200.):
    cells=design['modules']['top']['cells'];drivers={};loads=defaultdict(list)
    clocks={b for c in cells.values() for p,bs in c['connections'].items() if p in ('CLK','CLKA','CLKB') for b in bs}
    for name,c in cells.items():
        if name not in owner:continue
        for pin,bits in c['connections'].items():
            for bit in bits:
                if bit in clocks:continue
                if c['port_directions'][pin]=='output':drivers[bit]=name
                elif pin not in ('CLK','CLKA','CLKB','CIN'):loads[bit].append((name,pin))
    result=defaultdict(float)
    for bit,sinks in loads.items():
        source=drivers.get(bit)
        if source is None:continue
        for name,pin in sinks:
            a,b=owner[source],owner[name]
            if a!=b:result[a,b]+=(1+39*priorities.get((name,pin),0.)**2)/math.sqrt(len(sinks))+path_gain*(path_weights or {}).get((name,pin),0.)
    return [(a,b,w) for (a,b),w in sorted(result.items())]

def relax(rows,edge_rows,device,iterations=160,density=.65,anchor_strength=.025):
    names=sorted(rows);ids={n:i for i,n in enumerate(names)};initial=np.array([rows[n]['point'] for n in names],float);pos=initial.copy()
    moving=np.array([rows[n]['kind']!='fixed' for n in names]);area=np.array([rows[n]['area'] for n in names]);a=np.array([ids[x] for x,y,w in edge_rows]);b=np.array([ids[y] for x,y,w in edge_rows]);w=np.array([w for x,y,w in edge_rows])
    degree=np.bincount(np.r_[a,b],weights=np.r_[w,w],minlength=len(names));bw=4;nr=(device.rows+bw-1)//bw;nc=(device.cols+bw-1)//bw
    capacity=np.zeros(nr*nc)
    for r,row in enumerate(device.grid):
        for c,typ in enumerate(row):
            if typ==17:capacity[(r//bw)*nc+c//bw]+=density
    def metric(p):return float(np.sum(np.sum(np.abs(p[a]-p[b]),axis=1)*w))
    initial_cost=metric(pos);history=[]
    for iteration in range(iterations):
        sums=np.zeros_like(pos);np.add.at(sums,a,pos[b]*w[:,None]);np.add.at(sums,b,pos[a]*w[:,None])
        anchor=anchor_strength*degree+.1;target=(sums+initial*anchor[:,None])/np.maximum((degree+anchor)[:,None],1e-12)
        delta=.3*(target-pos);delta=np.clip(delta,-2.,2.)
        bins=np.clip((pos[:,0]/bw).astype(int),0,nr-1)*nc+np.clip((pos[:,1]/bw).astype(int),0,nc-1)
        mass=np.bincount(bins,weights=area,minlength=nr*nc);overflow=np.maximum(0,mass-capacity)/np.maximum(capacity,1.)
        centers=np.column_stack(((bins//nc+.5)*bw,(bins%nc+.5)*bw));direction=pos-centers
        norm=np.sqrt(np.sum(direction**2,axis=1));degenerate=norm<.1
        phase=np.arange(len(names))*2.399963229728653;direction[degenerate]=np.column_stack((np.sin(phase),np.cos(phase)))[degenerate];norm[degenerate]=1.
        delta+=direction/np.maximum(norm[:,None],.1)*np.minimum(overflow[bins],2.)[:,None]*1.5
        pos[moving]+=delta[moving];pos[moving,0]=np.clip(pos[moving,0],0,device.rows-1);pos[moving,1]=np.clip(pos[moving,1],0,device.cols-1)
        if iteration%20==19:history.append(dict(iteration=iteration+1,weighted_wirelength=metric(pos),overflow_area=float(np.maximum(0,mass-capacity).sum())))
    return {n:pos[i].tolist() for n,i in ids.items()},dict(initial_weighted_wirelength=initial_cost,continuous_weighted_wirelength=metric(pos),history=history,iterations=iterations,target_density=density,bin_width=bw)

def legalize(design,placed,rows,targets,edge_rows,device):
    result=copy.deepcopy(placed);sites=np.array([(r,c) for r,row in enumerate(device.grid) for c,t in enumerate(row) if t==17],int)
    reserved={(v['row'],v['column']+i) for v in placed.get('hard_clusters',{}).values() for i in range(3)};occupied=set();ff_controls=set();chosen={}
    importance=defaultdict(float)
    for a,b,w in edge_rows:importance[a]+=w;importance[b]+=w
    macros=sorted([n for n,v in rows.items() if v['kind'] in ('carry','wide')],key=lambda n:(-rows[n]['width'],-importance[n],n))
    for name in macros:
        unit=rows[name];width=unit['width'];target=np.array(targets[name]);centers=sites.astype(float);centers[:,1]+=(width-1)/2
        order=np.argsort(np.sum((centers-target)**2,axis=1),kind='stable');point=None
        for index in order:
            r,c=map(int,sites[index])
            if c+width<=device.cols and all(device.grid[r][c+j]==17 and (r,c+j) not in reserved for j in range(width)):point=r,c;break
        if point is None:raise ValueError('Global macro legalization ran out of contiguous CFUs')
        r,c=point;reserved.update((r,c+j) for j in range(width));chosen[name]=(r,c+(width-1)/2)
        if unit['kind']=='carry':
            for member in unit['members']:
                old=placed['arithmetic_clusters'][member];result['arithmetic_clusters'][member].update(row=r,column=c+old['column']-unit['left'])
        else:
            for member in unit['members']:
                if member in result['clusters']:result['clusters'][member].update(row=r,column=c)
                if member in result.get('mux_clusters',{}):result['mux_clusters'][member].update(row=r,column=c)
            macro=next(m for m in result['wide_macros'] if m['root']==unit['root']);macro.update(row=r,column=c)
    def has_ff(name):return rows[name]['kind']=='shared' or bool(placed['clusters'][rows[name]['cluster']]['ff'])
    order=sorted([n for n,v in rows.items() if v['kind'] in ('logic','shared')],key=lambda n:(not has_ff(n),-importance[n],n))
    for name in order:
        shared=rows[name]['kind']=='shared';keys=rows[name]['clusters'] if shared else [rows[name]['cluster']]
        cluster=result['clusters'][keys[0]];ff=cluster['ff'];target=np.array(targets[name]);indices=np.argsort(np.sum((sites-target)**2,axis=1),kind='stable');point=None
        for index in indices:
            r,c=map(int,sites[index])
            if (r,c) in reserved:continue
            for slot in ((0,2,4) if ff else range(8)):
                if all((r,c,slot+i) not in occupied for i in range(len(keys))) and (not ff or (r,c,slot//2) not in ff_controls):point=r,c,slot;break
            if point:break
        if point is None:raise ValueError('Global LUT/FF legalization exhausted capacity')
        r,c,slot=point;occupied.update((r,c,slot+i) for i in range(len(keys)))
        if ff:ff_controls.add((r,c,slot//2))
        for i,key in enumerate(keys):result['clusters'][key].update(row=r,column=c,slot=slot+i)
        chosen[name]=(r,c)
    for name,v in rows.items():
        if v['kind']=='fixed':chosen[name]=v['point']
    cost=sum(w*(abs(chosen[a][0]-chosen[b][0])+abs(chosen[a][1]-chosen[b][1])) for a,b,w in edge_rows)
    from ownpr_mux_macros import validate as wide_validate
    from ownpr_carry_macros import validate as carry_validate
    from ownpr_ff_configuration import compile_initial
    from ownpr_hard_architecture import HardArchitecture
    wide_validate(design,result,device);carry_validate(design,result,device);compile_initial(design,result);HardArchitecture().validate(design,result,device)
    if result.get('ioreg_clusters'):
        from ownpr_ioreg_macros import validate as validate_ioreg
        validate_ioreg(design,result,device)
    if result['fixed_io']!=placed['fixed_io'] or result.get('hard_clusters',{})!=placed.get('hard_clusters',{}) or result.get('ioreg_clusters',{})!=placed.get('ioreg_clusters',{}):raise ValueError('Fixed physical boundaries changed')
    result.pop('weighted_internal_manhattan',None);result.update(timing_signoff=False,physical_legality_complete=False)
    return result,dict(legal_weighted_wirelength=cost,moved_units=sum(tuple(rows[n]['point'])!=tuple(chosen[n]) for n in rows),units=len(rows))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('base','sta','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--pin-feedback',type=Path,help='Optional independently calibrated internal-pin feedback directory')
    p.add_argument('--iterations',type=int,default=160);p.add_argument('--density',type=float,default=.65)
    p.add_argument('--path-weighted',action='store_true',help='Add full-path demand without fanout dilution and reduce initial-location anchoring')
    a=p.parse_args()
    if not 1<=a.iterations<=500 or not .1<=a.density<=.9:p.error('Invalid global placement budget/density')
    reserve(ROOT,100_000_000);started=time.monotonic();out=a.out.resolve();out.mkdir(exist_ok=False)
    design=load_json(a.base/'packed.json');placed=load_json(a.base/'placement.json');sta=load_json(a.sta/'manifest.json')
    if sta['packed_sha256']!=sha(a.base/'packed.json') or sta['placement_sha256']!=sha(a.base/'placement.json'):raise ValueError('Global placement source changed')
    priorities,feedback=pin_weights(a.sta,design,4.,a.pin_feedback);device=Device();groups,owner=units(design,placed)
    demand,info=path_demands(a.sta,design) if a.path_weighted else ({},None)
    edge_rows=edges(design,owner,priorities,demand)
    targets,continuous=relax(groups,edge_rows,device,a.iterations,a.density,anchor_strength=.0025 if a.path_weighted else .025);candidate,legal=legalize(design,placed,groups,targets,edge_rows,device)
    continuous.update(path_weighted=a.path_weighted,path_demand=info,path_gain=200. if a.path_weighted else 0.)
    shutil.copyfile(a.base/'packed.json',out/'packed.json');(out/'placement.json').write_text(json.dumps(candidate,indent=2));(out/'continuous.json').write_text(json.dumps(dict(targets=targets,metrics=continuous),indent=2))
    shutil.copyfile(__file__,out/'operator_source.py.txt')
    meta=dict(status='placed',netlist_sha256=sha(out/'packed.json'),placement_sha256=sha(out/'placement.json'),source_placement=str(a.base.resolve()),source_placement_sha256=sha(a.base/'placement.json'),source_sta_sha256=sha(a.sta/'probe.tr'),
        operator_sha256=sha(out/'operator_source.py.txt'),feedback=feedback,continuous=continuous,legal=legal,wall_seconds=time.monotonic()-started,logical_netlist_byte_identical=True,
        fixed_pins_unchanged=True,hard_sites_fixed=True,interface_cycles_changed=False,clock_endpoints_unchanged=False,vendor_pnr_executed=False,timing_signoff=False,
        scope='Whole-design soft-logic/carry/wide placement candidate; preserved packing; clock and data rerouting plus full STA required')
    (out/'manifest.json').write_text(json.dumps(meta,indent=2)+'\n');print(json.dumps(meta,indent=2))

if __name__=='__main__':main()
