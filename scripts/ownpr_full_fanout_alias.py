"""Redistribute complete receiver sets among proven existing state replicas.

All old cells, state and coordinates stay present. Geometry/load estimates
propose assignments only; complete changed trees and setup/hold DAGs decide
admission. Induction is proved in both the before and after graphs.
"""
import argparse,copy,json,math,shutil,time
from collections import Counter,defaultdict
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from ownpr_existing_state_alias import alias_groups,admissible
from ownpr_state_safety import protected_cells,active
from ownpr_critical_chain_move import locations
from ownpr_register_equivalence import collapse
from native_boundary_cec import prove
from ownpr_bidir_timing import analyze
from workspace_budget import reserve


def fold_many(design,aliases):
    d=copy.deepcopy(design);m=d['modules']['top'];cells=m['cells'];bits={cells[n]['connections']['Q'][0]:cells[p]['connections']['Q'][0] for n,p in aliases.items()}
    if set(aliases)&set(aliases.values()):raise ValueError('Alias representatives must be roots')
    for n in aliases:del cells[n]
    for c in cells.values():c['connections']={p:[bits.get(b,b) for b in bs] for p,bs in c['connections'].items()}
    for key in ('netnames','ports'):
        for row in m.get(key,{}).values():row['bits']=[bits.get(b,b) for b in row['bits']]
    return d


def certify(before,after,aliases,out):
    out.mkdir(exist_ok=False);stages=[];folded=[]
    for label,d in (('before',before),('after',after)):
        reference=fold_many(d,aliases);f,a,b,steps=collapse(reference,d,aliases)
        if f!=reference:raise ValueError('Joint alias folding differs')
        proof=prove(a,b,out/label,120)
        if a!=b and not proof or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Joint alias induction failed')
        stages.append(dict(stage=label,initial_and_transition=steps,transition_structurally_identical=a==b,proof=proof));folded.append(f)
    if folded[0]!=folded[1]:raise ValueError('Load reassignment changed folded observations')
    return dict(status='joint_existing_alias_assignment_proved',stages=stages,folded_observations_identical=True,scope='Known equal initialization and equal synchronous next-state under the joint alias invariant, before and after. No CDC or waveform claim.')


def propose(design,layout,model,seeds,min_fanout=32,max_families=4):
    cells=design['modules']['top']['cells'];points=locations(layout);protected=protected_cells(design);parent={}
    loads=Counter(b for c in cells.values() for p,bs in c['connections'].items() if c['port_directions'][p]=='input' for b in bs)
    def root(n):
        parent.setdefault(n,n)
        while parent[n]!=n:parent[n]=parent[parent[n]];n=parent[n]
        return n
    def union(a,b):
        a,b=root(a),root(b)
        if a!=b:parent[max(a,b)]=min(a,b)
    for group in alias_groups(design):
        for n in group[1:]:union(group[0],n)
    for clone,source in seeds.items():union(clone,source)
    groups=defaultdict(list)
    for n in parent:groups[root(n)].append(n)
    setup=analyze(model['late'],model['setup_requirements']);hold=analyze(model['early'],model['hold_requirements'],True)
    kept={b for row in design['modules']['top'].get('netnames',{}).values() if any(k.lower() in ('keep','syn_keep') and active(v) for k,v in row.get('attributes',{}).items()) for b in row['bits']}
    wire={e['destination']:e for e in model['late']['edges'] if e['kind']=='wire'};options=[]
    for members in groups.values():
        members=sorted(members)
        if any(n in protected or n not in points or n+'/Q' not in model['late']['seeds'] or not admissible(cells[n]) or set(cells[n].get('parameters',{}))-{'INIT'} for n in members):continue
        total=sum(loads[cells[n]['connections']['Q'][0]] for n in members)
        if total<min_fanout:continue
        marked=[n for n in members if cells[n]['connections']['Q'][0] in kept]
        if len(marked)>1:continue
        representative=marked[0] if marked else min(members,key=lambda n:(-loads[cells[n]['connections']['Q'][0]],n))
        worst=min(min(setup['slack'][n+'/Q']) for n in members)
        if worst>=0:continue
        bitnames={cells[n]['connections']['Q'][0]:n for n in members};sinks=[];retained=[]
        for name,c in cells.items():
            for pin,bs in c['connections'].items():
                if c['port_directions'][pin]!='input':continue
                if len(bs)!=1:
                    retained.extend(dict(instance=name,pin=pin,index=i,driver=bitnames[b]) for i,b in enumerate(bs) if b in bitnames)
                    continue
                if bs[0] not in bitnames:continue
                old=bitnames[bs[0]];key=name+'/'+pin;typ=c['type']
                supported=(typ in ('LUT1','LUT2','LUT3','LUT4') and pin.startswith('I') or typ=='ALU' and pin in ('I0','I1','I3') or typ.startswith('MUX2_LUT') and pin=='S0')
                if not supported or name in protected or name not in points or key not in wire:retained.append(dict(instance=name,pin=pin,driver=old));continue
                sinks.append(dict(instance=name,pin=pin,old=old,key=key,slack=min(setup['slack'][key]),hold=min(hold['slack'][key]),wire=max(wire[key]['delay'])))
        if len(sinks)<len(members):continue
        options.append(dict(members=members,representative=representative,total_receivers=total,retained=retained,sinks=sinks,worst_slack_ns=worst))
    options.sort(key=lambda r:(r['worst_slack_ns'],-r['total_receivers'],r['representative']));new=copy.deepcopy(design);changes=[];aliases={}
    distance=lambda a,b:sum(abs(x-y) for x,y in zip(a,b))
    for family in options[:max_families]:
        members=family['members'];assigned=Counter(r['driver'] for r in family['retained']);target=math.ceil(family['total_receivers']/len(members));capacity=max(target+4,math.ceil(target*1.2));alloc=[]
        # High-risk receivers choose first; every remaining receiver still
        # participates and every retained unsupported receiver is counted.
        for s in sorted(family['sinks'],key=lambda r:(r['slack'],-r['wire'],r['instance'],r['pin'])):
            dest=points[s['instance']];old=s['old'];oldload=loads[cells[old]['connections']['Q'][0]];oldspan=distance(points[old],dest)
            def cost(n):
                span=distance(points[n],dest);load=assigned[n]+1
                # This is a proposal proxy, explicitly not a measured delay.
                estimate=s['wire']*((load+4)/(oldload+4))**.35*(1+.12*span)/(1+.12*oldspan)
                return estimate+max(model['late']['seeds'][n+'/Q'])+.05*max(0,load-target),int(n!=old),n
            choices=[n for n in members if assigned[n]<capacity]
            if not choices:choices=members
            selected=min(choices,key=cost);score=cost(selected)[0];assigned[selected]+=1
            new['modules']['top']['cells'][s['instance']]['connections'][s['pin']]=list(cells[selected]['connections']['Q'])
            alloc.append(dict(s,selected=selected,estimated_cost=score))
        if sum(assigned.values())!=family['total_receivers']:raise ValueError('Receiver enumeration lost a branch')
        family.update(allocations=alloc,after_loads=dict(assigned),before_loads={n:loads[cells[n]['connections']['Q'][0]] for n in members},changed_receivers=sum(s['selected']!=s['old'] for s in alloc),proposal_scope='Full receiver enumeration; geometry/load proxy proposes only. Whole-tree measurement, full setup/hold feedback and native STA required.')
        if family['changed_receivers']:
            changes.append(family);aliases.update({n:family['representative'] for n in members if n!=family['representative']})
    if not aliases:raise ValueError('No full-fanout alias assignment candidate')
    return new,changes,aliases


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('base','model','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--families',type=int,default=4);p.add_argument('--min-fanout',type=int,default=32);a=p.parse_args()
    if not 1<=a.families<=6 or not 2<=a.min_fanout<=256:p.error('Invalid bounded assignment')
    reserve(ROOT,50_000_000);start=time.monotonic();old=load_json(a.base/'packed.json');layout=load_json(a.base/'placement.json');meta=load_json(a.base/'manifest.json');mm=load_json(a.model/'manifest.json')
    if meta['status']!='placed' or meta['netlist_sha256']!=sha(a.base/'packed.json') or meta['placement_sha256']!=sha(a.base/'placement.json'):raise ValueError('Changed source placement')
    for path,h in mm['source_sha256'].items():
        if sha(Path(path))!=h:raise ValueError('Changed model source')
    if mm['status']!='validated_setup_hold_feedback' or mm['model_sha256']!=sha(a.model/'model.json'):raise ValueError('Unvalidated setup/hold model')
    if mm['source_sha256'].get(str((a.base/'packed.json').resolve()))!=sha(a.base/'packed.json'):raise ValueError('Model belongs to another netlist')
    new,changes,aliases=propose(old,layout,load_json(a.model/'model.json'),meta.get('register_replicas',{}),a.min_fanout,a.families);a.out.mkdir(exist_ok=False);proof=certify(old,new,aliases,a.out/'proof')
    (a.out/'packed.json').write_text(json.dumps(new,indent=2));shutil.copyfile(a.base/'placement.json',a.out/'placement.json');(a.out/'assignment.json').write_text(json.dumps(changes,indent=2));(a.out/'proof.json').write_text(json.dumps(proof,indent=2))
    for source in (Path(__file__),ROOT/'scripts/ownpr_bidir_timing.py',ROOT/'scripts/ownpr_register_equivalence.py',ROOT/'scripts/ownpr_state_safety.py'):shutil.copyfile(source,a.out/(source.name+'.txt'))
    m=dict(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),source_netlist_sha256=sha(a.base/'packed.json'),source_placement_sha256=sha(a.base/'placement.json'),source_model_sha256=sha(a.model/'model.json'),register_aliases=aliases,proof=proof,original_units_byte_identical=True,lut_delta=0,register_delta=0,clock_endpoints_unchanged=True,interface_cycles_changed=False,timing_signoff=False,vendor_pnr_executed=False,wall_seconds=time.monotonic()-start)
    (a.out/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps(dict(families=len(changes),receivers=sum(r['total_receivers'] for r in changes),changed_receivers=sum(r['changed_receivers'] for r in changes),loads=[r['after_loads'] for r in changes],wall_seconds=m['wall_seconds']),indent=2))


if __name__=='__main__':main()
