"""Remove idempotent repeated carry stages with unobserved SUM outputs.

For equal mode and I0/I1/I3, carry is f(c)=p?c:g, so f(f(c))=f(c).
This frequently occurs in sign extension. No bit-width or signal-name rule is
used. Complete hard-boundary CEC is mandatory before physical implementation.
"""
import argparse,copy,json
from collections import Counter
from pathlib import Path
from ownpr_carry_chains import extract
from ownpr_carry_outputs import materialize
from native_boundary_cec import functions,prove
from ownpr_place import sha
from ownpr_device import ROOT
from workspace_budget import reserve


def simplify(design,top='top'):
    out=copy.deepcopy(design);m=out['modules'][top];cells=m['cells'];chains=extract(m)
    if chains['blockers']:raise ValueError('Carry simplification excludes forks')
    loads=Counter();observed=set();constants={'0':0,'1':1}
    for c in cells.values():
        if c['type'] in ('VCC','GND'):constants[next(iter(c['connections'].values()))[0]]=int(c['type']=='VCC')
        for p,bs in c['connections'].items():
            if c['port_directions'][p]=='input':loads.update(bs)
    for row in m['ports'].values():observed.update(row['bits'])
    for row in m.get('netnames',{}).values():
        keep=row.get('attributes',{}).get('keep','0')
        if keep if isinstance(keep,int) else int(keep,2):observed.update(row['bits'])
    aliases={};removed=[]
    def resolve(b):
        while b in aliases:b=aliases[b]
        return b
    def signature(c):return int(c['parameters']['ALU_MODE'],2),tuple(('c',constants[b]) if b in constants else ('s',resolve(b)) for b in (c['connections'][p][0] for p in ('I0','I1','I3')))
    for chain in chains['chains']:
        previous=None
        for name in chain['cells']:
            c=cells[name];summ=c['connections']['SUM'][0];ci=resolve(c['connections']['CIN'][0]);co=c['connections']['COUT'][0]
            if previous is not None and signature(c)==signature(cells[previous]) and not loads[summ] and summ not in observed:
                truth=functions(c)[1][2]
                for value in range(16):
                    first=(truth>>value)&1;second=(truth>>((value&7)|(first<<3)))&1
                    if first!=second:raise ValueError('Carry function is not idempotent')
                aliases[co]=ci;removed.append(dict(instance=name,previous=previous,old_cout=co,replacement_cin=ci));del cells[name]
            else:previous=name
    for c in cells.values():
        c['connections']={p:[resolve(b) for b in bs] for p,bs in c['connections'].items()}
    for kind in ('ports','netnames'):
        for row in m.get(kind,{}).values():row['bits']=[resolve(b) for b in row['bits']]
    return out,removed


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--netlist',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--materialize',action='store_true');a=p.parse_args()
    reserve(ROOT,50_000_000);a.out.mkdir(exist_ok=False);old=json.loads(a.netlist.read_text());new,removed=simplify(old)
    materialized=[]
    if a.materialize:new,materialized=materialize(new)
    proof=prove(old,new,a.out/'cec',120);(a.out/'netlist.json').write_text(json.dumps(new,indent=2))
    result=dict(status='equivalent_carry_simplification',removed=removed,materialized=materialized,proof=proof,input_sha256=sha(a.netlist),output_sha256=sha(a.out/'netlist.json'),
        state_and_interface_cycles_preserved=True,physical_legality_verified=False,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print('removed',len(removed),'materialized',len(materialized))
