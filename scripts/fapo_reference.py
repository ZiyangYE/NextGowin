"""Independent bounded reference for FAPO's published core ideas.

Implements the paper's fanout recurrence, final live-area accounting, cut
inlining, replication/recovery, and minimum vertex-cut coverage. Search limits
are explicit project choices; this is not the authors' unpublished C++ code.
The alpha model is dimensionless and is not a calibrated Gowin timing model.
"""
import argparse
from collections import defaultdict,deque
import copy
import hashlib
import json
import math
from pathlib import Path
import time
from gowin_blif import read_blif
from run_abc_reproduction import ROOT,cec


def seed(network):
    return dict(inputs=list(network['inputs']),ports=list(network['outputs']),outputs=list(network['outputs']),
        nodes={n['output']:dict(inputs=list(n['inputs']),truth=n['truth'],root=n['output']) for n in network['nodes']})


def clean(state,k):
    s=copy.deepcopy(state);live=set();stack=list(s['outputs']);pis=set(s['inputs'])
    while stack:
        name=stack.pop()
        if name in pis or name in live:
            continue
        if name not in s['nodes']:
            raise ValueError('Undriven optimization state')
        live.add(name);stack.extend(s['nodes'][name]['inputs'])
    s['nodes']={name:node for name,node in s['nodes'].items() if name in live}
    pending={};sinks=defaultdict(list)
    for name,node in s['nodes'].items():
        n=len(node['inputs'])
        if n>k or node['truth']<0 or node['truth'].bit_length()>(1<<n):
            raise ValueError('Illegal LUT cut/truth table')
        parents=set(node['inputs']) & s['nodes'].keys();pending[name]=len(parents)
        for parent in parents:
            sinks[parent].append(name)
    queue=deque(name for name,n in pending.items() if not n);order=[]
    while queue:
        name=queue.popleft();order.append(name)
        for child in sinks[name]:
            pending[child]-=1
            if not pending[child]:queue.append(child)
    if len(order)!=len(s['nodes']):
        raise ValueError('Optimization created a combinational cycle')
    s['order']=order
    return s


def timing(state,alpha):
    fanout={name:0 for name in state['inputs']+list(state['nodes'])}
    sinks=defaultdict(list)
    for name,node in state['nodes'].items():
        for index,parent in enumerate(node['inputs']):
            fanout[parent]+=1;sinks[parent].append(('node',name,index))
    for index,driver in enumerate(state['outputs']):
        fanout[driver]+=1;sinks[driver].append(('output',index,None))
    weight={p:alpha*max(fanout[p]-1,0) for p in state['inputs']}
    arrival=dict(weight)
    for name in state['order']:
        parents=state['nodes'][name]['inputs']
        # Constants are physical VCC/GND, not a timed LUT instance.
        weight[name]=1+alpha*max(fanout[name]-1,0) if parents else 0
        arrival[name]=weight[name]+max((arrival[p] for p in parents),default=0)
    delay=max(arrival[p] for p in state['outputs'])
    tail={name:0 for name in arrival}
    for name in reversed(state['inputs']+state['order']):
        tail[name]=weight[name]+max((tail[sink] for kind,sink,_ in sinks[name] if kind=='node'),default=0)
    critical={name for name in arrival if abs(arrival[name]+tail[name]-weight[name]-delay)<1e-9}
    edges=[(parent,name) for name in state['nodes'] for parent in state['nodes'][name]['inputs']
           if parent in critical and name in critical and abs(arrival[parent]+tail[name]-delay)<1e-9]
    area=sum(bool(n['inputs']) for n in state['nodes'].values())
    return dict(area=area,delay=delay,atp=area*delay,fanout=fanout,sinks=sinks,
                arrival=arrival,tail=tail,weight=weight,critical=critical,critical_edges=edges)


def minimum_copy_cut(state,alpha):
    t=timing(state,alpha);vertices=sorted(t['critical']);index={v:i for i,v in enumerate(vertices)}
    eligible={v for v in vertices if v in state['nodes'] and state['nodes'][v]['inputs'] and t['fanout'][v]>1}
    inf=len(eligible)+1;source=2*len(vertices);sink=source+1;graph=[{} for _ in range(sink+1)]
    def add(a,b,capacity):
        graph[a][b]=graph[a].get(b,0)+capacity;graph[b].setdefault(a,0)
    for v in vertices:
        i=index[v];add(2*i,2*i+1,1 if v in eligible else inf)
        if (v in state['inputs'] or not state['nodes'][v]['inputs']) and abs(t['tail'][v]-t['delay'])<1e-9:
            add(source,2*i,inf)
    for a,b in set(t['critical_edges']):
        add(2*index[a]+1,2*index[b],inf)
    for driver in set(state['outputs']):
        if driver in index and abs(t['arrival'][driver]-t['delay'])<1e-9:
            add(2*index[driver]+1,sink,inf)
    flow=0
    while True:
        parent={source:None};queue=deque([source])
        while queue and sink not in parent:
            a=queue.popleft()
            for b,capacity in graph[a].items():
                if capacity>0 and b not in parent:
                    parent[b]=a;queue.append(b)
        if sink not in parent:
            break
        path=[];b=sink
        while parent[b] is not None:
            a=parent[b];path.append((a,b));b=a
        amount=min(graph[a][b] for a,b in path);flow+=amount
        if flow>=inf:
            return []
        for a,b in path:
            graph[a][b]-=amount;graph[b][a]+=amount
    reachable={source};queue=deque([source])
    while queue:
        for b,capacity in graph[queue.popleft()].items():
            if capacity>0 and b not in reachable:
                reachable.add(b);queue.append(b)
    return [v for v in sorted(eligible) if 2*index[v] in reachable and 2*index[v]+1 not in reachable]


def redirect(state,old,new,selected=None):
    s=copy.deepcopy(state)
    if selected is None:
        selected=[('node',name,i) for name,n in s['nodes'].items() for i,p in enumerate(n['inputs']) if p==old]
        selected += [('output',i,None) for i,p in enumerate(s['outputs']) if p==old]
    for kind,target,index in selected:
        if kind=='node':s['nodes'][target]['inputs'][index]=new
        else:s['outputs'][target]=new
    return s


def replicate(state,name,alpha):
    t=timing(state,alpha);sinks=list(t['sinks'][name])
    if len(sinks)<2 or not state['nodes'][name]['inputs']:
        return None,None
    sinks.sort(key=lambda x:(-t['tail'][x[1]] if x[0]=='node' else 0,str(x)))
    clone=name+'__copy';number=0
    while clone in state['nodes'] or clone in state['inputs']:
        number+=1;clone=name+'__copy'+str(number)
    s=redirect(state,name,clone,sinks[1::2]);s['nodes'][clone]=copy.deepcopy(state['nodes'][name])
    return s,clone


def inline(state,name,parent,k):
    old=state['nodes'][name];p=state['nodes'][parent]
    leaves=list(dict.fromkeys(x for old_input in old['inputs'] for x in (p['inputs'] if old_input==parent else [old_input])))
    if len(leaves)>k:
        return None
    truth=0
    for assignment in range(1<<len(leaves)):
        values={v:(assignment>>i)&1 for i,v in enumerate(leaves)}
        pindex=sum(values[v]<<i for i,v in enumerate(p['inputs']))
        value=(p['truth']>>pindex)&1
        index=sum((value if v==parent else values[v])<<i for i,v in enumerate(old['inputs']))
        truth|=((old['truth']>>index)&1)<<assignment
    support=[i for i in range(len(leaves)) if any(((truth>>x)&1)!=((truth>>(x^(1<<i)))&1) for x in range(1<<len(leaves)))]
    reduced=0
    for assignment in range(1<<len(support)):
        original=sum(((assignment>>i)&1)<<v for i,v in enumerate(support))
        reduced|=((truth>>original)&1)<<assignment
    s=copy.deepcopy(state);s['nodes'][name]=dict(inputs=[leaves[i] for i in support],truth=reduced,root=old['root'])
    return clean(s,k)


def recovery_options(state,k):
    groups=defaultdict(list)
    for name,node in state['nodes'].items():groups[node['root']].append(name)
    for names in groups.values():
        for keep in names:
            for remove in names:
                if keep==remove:continue
                trial=redirect(state,remove,keep)
                try:yield clean(trial,k)
                except ValueError:continue


def recover(state,k,alpha):
    current=clean(state,k)
    while True:
        old=timing(current,alpha);best=None;best_key=None
        for trial in recovery_options(current,k):
            t=timing(trial,alpha)
            if t['area']<old['area'] and t['delay']<=old['delay']:
                key=(t['atp'],t['delay'],t['area'])
                if best_key is None or key<best_key:best,best_key=trial,key
        if best is None:return current
        current=best


def fingerprint(state):
    return (tuple(state['outputs']),tuple((name,tuple(n['inputs']),n['truth'],n['root']) for name,n in sorted(state['nodes'].items())))


def expand(state,k,alpha,max_roots):
    t=timing(state,alpha)
    names=sorted(state['nodes'],key=lambda n:(n not in t['critical'],-t['fanout'][n],-t['arrival'][n],n))[:max_roots]
    for name in names:
        for parent in sorted(set(state['nodes'][name]['inputs']) & state['nodes'].keys()):
            trial=inline(state,name,parent,k)
            if trial is not None:yield trial,'cut_switch'
    for name in names:
        replica,clone=replicate(state,name,alpha)
        if replica is None:continue
        replica=clean(replica,k);yield replica,'replicate'
        for target in [name,clone]:
            for parent in sorted(set(replica['nodes'][target]['inputs']) & replica['nodes'].keys()):
                trial=inline(replica,target,parent,k)
                if trial is not None:yield trial,'replicate_and_switch'
    for trial in recovery_options(state,k):yield trial,'recovery'


def bundle(state,k,alpha,cap,rounds=4):
    current=state;history=[]
    for _ in range(rounds):
        cover=minimum_copy_cut(current,alpha)
        if not cover:break
        trial=current
        for name in cover:
            trial,_=replicate(trial,name,alpha)
            if trial is None:return current,history
            trial=clean(trial,k)
        trial=recover(trial,k,alpha);old=timing(current,alpha);new=timing(trial,alpha)
        if new['area']>cap or new['delay']>=old['delay']:break
        current=trial;history.append(dict(cover=cover,area=new['area'],delay=new['delay']))
    return current,history


def pareto(items,limit):
    kept=[];seen=set()
    for s,t in sorted(items,key=lambda x:(x[1]['atp'],x[1]['delay'],x[1]['area'])):
        key=fingerprint(s)
        if key in seen:continue
        if any(u['area']<=t['area'] and u['delay']<=t['delay'] and (u['area']<t['area'] or u['delay']<t['delay']) for _,u in items):continue
        kept.append((s,t));seen.add(key)
        if len(kept)>=limit:break
    return kept


def optimize(initial,k=4,alpha=1,beta=.05,sweeps=4,max_roots=12,proposal_limit=128,cut_enabled=True):
    if not math.isfinite(alpha) or alpha<0 or not math.isfinite(beta) or beta<0:
        raise ValueError('Invalid alpha/area allowance')
    if sweeps<0 or max_roots<1 or proposal_limit<1:
        raise ValueError('Invalid search limit')
    initial=clean(initial,k);base=timing(initial,alpha);cap=base['area']+math.floor(beta*base['area'])
    archive=[(initial,base)];frontier=archive;seen={fingerprint(initial)};history=[]
    for sweep in range(sweeps):
        evaluated=[];counts=defaultdict(int)
        for state,_ in frontier:
            for proposal,kind in expand(state,k,alpha,max_roots):
                if sum(counts.values())>=proposal_limit:break
                counts[kind]+=1
                for candidate in [clean(proposal,k),recover(proposal,k,alpha)]:
                    key=fingerprint(candidate)
                    if key in seen:continue
                    seen.add(key);t=timing(candidate,alpha)
                    if t['area']<=cap:evaluated.append((candidate,t))
        if not evaluated:break
        cuts=[]
        if cut_enabled and alpha>0 and beta>0:
            parent=min(evaluated,key=lambda x:x[1]['atp'])
            trial,actions=bundle(parent[0],k,alpha,cap)
            if actions:
                cuts=[(trial,timing(trial,alpha))]
                history.append(dict(sweep=sweep,minimum_cut_actions=actions))
        archive=pareto(archive+evaluated+cuts,32)
        frontier=pareto(frontier+evaluated,4)
        history.append(dict(sweep=sweep,proposals=dict(counts),area_feasible=len(evaluated)))
    finalized=[(initial,base)]
    for state,_ in archive:
        state=recover(state,k,alpha);t=timing(state,alpha)
        if t['area']<=cap:finalized.append((state,t))
    best=min(finalized,key=lambda x:(x[1]['atp'],x[1]['delay'],x[1]['area']))
    return best[0],dict(seed={key:base[key] for key in ['area','delay','atp']},
        selected={key:best[1][key] for key in ['area','delay','atp']},area_cap=cap,history=history)


def write_blif(state):
    reserved=set(state['inputs']+state['ports']);prefix='__fapo_'
    while any(p.startswith(prefix) for p in reserved):prefix='_'+prefix
    names={name:prefix+str(i) for i,name in enumerate(state['nodes'])};names.update({p:p for p in state['inputs']})
    assigned=set()
    for port,driver in zip(state['ports'],state['outputs']):
        if driver in state['nodes'] and driver not in assigned:
            names[driver]=port;assigned.add(driver)
    lines=['.model fapo_reference','.inputs '+' '.join(state['inputs']),'.outputs '+' '.join(state['ports'])]
    for name in state['order']:
        n=state['nodes'][name];k=len(n['inputs'])
        lines.append('.names '+' '.join([names[p] for p in n['inputs']]+[names[name]]))
        if k==0:
            if n['truth']:lines.append('1')
        else:
            for x in range(1<<k):
                if (n['truth']>>x)&1:lines.append(''.join(str((x>>i)&1) for i in range(k))+' 1')
    for port,driver in zip(state['ports'],state['outputs']):
        if names[driver]!=port:lines += [f'.names {names[driver]} {port}','1 1']
    return '\n'.join(lines+['.end'])+'\n'


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('input',type=Path);ap.add_argument('--reference',type=Path)
    ap.add_argument('--out',type=Path,required=True);ap.add_argument('--lut',type=int,default=4,choices=[4,6])
    ap.add_argument('--alpha',type=float,default=1);ap.add_argument('--beta',type=float,default=.05)
    ap.add_argument('--sweeps',type=int,default=4);ap.add_argument('--max-roots',type=int,default=12)
    ap.add_argument('--greedy-only',action='store_true')
    args=ap.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    initial=seed(read_blif(args.input,max_inputs=args.lut))
    started=time.perf_counter()
    selected,result=optimize(initial,args.lut,args.alpha,args.beta,args.sweeps,args.max_roots,cut_enabled=not args.greedy_only)
    elapsed=time.perf_counter()-started
    target=out/'optimized.blif';target.write_text(write_blif(selected))
    serialized=clean(seed(read_blif(target,max_inputs=args.lut)),args.lut)
    actual=timing(serialized,args.alpha)
    if any(actual[key]!=result['selected'][key] for key in ['area','delay','atp']):
        raise ValueError('Output alias LUTs changed the area/timing accounting; output rejected')
    result.update(scope='Independent bounded FAPO reference; dimensionless fanout model, not calibrated Gowin timing',
        alpha=args.alpha,beta=args.beta,lut_size=args.lut,
        sweeps=args.sweeps,max_roots=args.max_roots,proposal_limit=128,
        cut_enabled=not args.greedy_only,
        optimization_wall_seconds=elapsed,
        source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
        output_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        cec=cec(ROOT/'build/abc/_TEST/abc.exe',(args.reference or args.input).resolve(),target,out/'cec.log',180))
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    if not result['cec']['equivalent']:raise RuntimeError('FAPO output equivalence unproven')


if __name__=='__main__':
    main()
