"""Reuse an existing synchronous state replica with an explicit invariant.

Discovery signatures are only a filter. Both pre/post transition relations
and all folded observations require CEC. Physical timing is not implied.
"""
import copy,hashlib,json
from pathlib import Path
from native_boundary_cec import initial,initial_values,prove
from ownpr_register_equivalence import collapse
from ownpr_multilevel_cut import Cuts
from ownpr_dead_logic import retained


def admissible(cell):
    attrs=cell.get('attributes',{})
    async_mark=any(k.lower()=='async_reg' and str(v).strip().lower() not in ('','false','no') and set(str(v).strip())!={'0'} for k,v in attrs.items())
    known={'src','module_not_derived','hdlname','hierarchy','unused_bits','keep','dont_touch','mark_debug','async_reg'}
    return cell['type'] in ('DFFRE','DFFSE') and not async_mark and not retained(attrs) and all(k.lower() in known for k in attrs)


def alias_groups(design):
    model=Cuts(design);cells=design['modules']['top']['cells'];init=initial_values(design['modules']['top']);cache={};active=set()
    def signature(bit):
        if bit in model.constants:return 'constant:'+model.constants[bit]
        if bit in cache:return cache[bit]
        name=model.drivers.get(bit)
        if name not in model.functions:return 'boundary:'+str(bit)
        if bit in active:raise ValueError('Combinational cycle in state signature')
        active.add(bit);inputs,_,truth=model.functions[name]
        result=hashlib.sha256(repr((truth,tuple(signature(b) for b in inputs))).encode()).hexdigest()
        active.remove(bit);cache[bit]=result;return result
    groups={}
    for name,c in cells.items():
        if not admissible(c):continue
        p=c['connections'];control='RESET' if c['type']=='DFFRE' else 'SET'
        if set(p)!={'D','Q','CLK','CE',control} or any(len(bs)!=1 for bs in p.values()):continue
        value=initial(init,c,p['Q'][0])
        if value not in ('0','1') or p['CLK'][0] in model.constants:continue
        canonical=lambda b:model.constants.get(b,b)
        key=(c['type'],json.dumps(c.get('parameters',{}),sort_keys=True),value,p['CLK'][0],canonical(p['CE'][0]),canonical(p[control][0]),signature(p['D'][0]))
        groups.setdefault(key,[]).append(name)
    return [sorted(v) for v in groups.values() if len(v)>1]


def fold(design,parent,replica):
    d=copy.deepcopy(design);m=d['modules']['top'];cells=m['cells'];old=cells[replica]['connections']['Q'][0];new=cells[parent]['connections']['Q'][0]
    del cells[replica]
    for c in cells.values():c['connections']={p:[new if b==old else b for b in bs] for p,bs in c['connections'].items()}
    for category in ('ports','netnames'):
        for row in m.get(category,{}).values():row['bits']=[new if b==old else b for b in row['bits']]
    return d


def rewire(design,parent,replica,sinks):
    cells=design['modules']['top']['cells']
    if parent==replica or not all(n in cells and admissible(cells[n]) for n in (parent,replica)):raise ValueError('Unsupported existing state alias')
    if not sinks or len(set(map(tuple,sinks)))!=len(sinks):raise ValueError('Missing/duplicate alias sink')
    a=cells[parent]['connections']['Q'];b=cells[replica]['connections']['Q']
    after=copy.deepcopy(design)
    for n,p in sinks:
        c=cells[n]
        if c['type'] not in ('LUT1','LUT2','LUT3','LUT4') or c['port_directions'].get(p)!='input' or c['connections'].get(p)!=a:raise ValueError('Alias target is not an ordinary LUT data input')
        after['modules']['top']['cells'][n]['connections'][p]=list(b)
    return after


def certify(before,after,parent,replica,out,timeout=120):
    # Restrict the proof to data rewiring: no hidden state or interface edits.
    for d in (before,after):
        flag=d['modules']['top'].get('attributes',{}).get('blackbox','0')
        if str(flag).strip().lower() not in ('','false') and set(str(flag).strip())!={'0'}:raise ValueError('Top-level blackbox cannot establish a state invariant')
        if not all(admissible(d['modules']['top']['cells'][n]) for n in (parent,replica)):raise ValueError('Unmodeled state/CDC attributes')
    out=Path(out);out.mkdir(exist_ok=False,parents=True);folded=[];steps=[]
    for label,d in [('before',before),('after',after)]:
        base=fold(d,parent,replica)
        f,a,b,transitions=collapse(base,d,{replica:parent})
        if f!=base:raise ValueError('State folding contract differs')
        proof=prove(a,b,out/label,timeout)
        if not proof and a!=b or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Existing state invariant failed')
        folded.append(f);steps.append(dict(stage=label,initial_and_transition=transitions,proof=proof))
    observations=prove(*folded,out/'observations',timeout)
    if not observations and folded[0]!=folded[1] or not all(r['cec']['equivalent'] for r in observations):raise ValueError('Alias rewiring changed observations')
    return dict(status='existing_state_alias_rewire_proved',parent=parent,replica=replica,steps=steps,observations=observations,
        scope='Known equal initial state, common positive clock, and equal synchronous D/CE/control under the invariant in both designs. No CDC/metastability or analog-waveform claim; full new STA mandatory.')
