"""Select whole calibrated carry chains using audited critical state endpoints.

Selection traces the equivalent pre-mapping graph, stopping at the first ALU
on each branch. It is a candidate policy, not a physical timing prediction.
"""
import argparse,json,re
from collections import defaultdict,deque
from pathlib import Path
from ownpr_image_storage import load_json,read_text,sha
from ownpr_carry_chains import extract
from ownpr_device import ROOT


def rank(design,endpoints):
    cells=design['modules']['top']['cells'];inventory=extract(design['modules']['top']);chains=inventory['chains']
    if inventory['blockers']:raise ValueError('Carry forks are unsupported')
    owner={n:i for i,c in enumerate(chains) for n in c['cells']};drivers={};loads=defaultdict(list)
    for name,c in cells.items():
        for pin,bits in c['connections'].items():
            for bit in bits:
                if c['port_directions'][pin]=='output':drivers[bit]=(name,pin)
                else:loads[bit].append((name,pin))
    candidates={}
    for endpoint in endpoints:
        ff,pin=endpoint['instance'],endpoint['pin'];cell=cells.get(ff)
        if not cell or not cell['type'].startswith('DFF') or pin=='CLK' or cell['port_directions'].get(pin)!='input':continue
        queue=deque((b,0) for b in cell['connections'][pin]);seen=set()
        while queue:
            bit,depth=queue.popleft()
            if bit in seen:continue
            seen.add(bit);name,output=drivers.get(bit,(None,None))
            if name is None:continue
            node=cells[name]
            if node['type']=='ALU':
                chain=chains[owner[name]];head=chain['cells'][0]
                eligible=(set(chain['modes'])=={2} and chain['head_constant'] in ('0','1') and all(loads[cells[n]['connections']['COUT'][0]]==[(chain['cells'][i+1],'CIN')] for i,n in enumerate(chain['cells'][:-1])))
                row=candidates.setdefault(head,dict(head=head,cells=chain['cells'],length=chain['length'],eligible=eligible,head_constant=chain['head_constant'],modes=chain['modes'],witnesses=[]))
                row['witnesses'].append(dict(endpoint=endpoint,logic_depth=depth,first_alu=name,first_pin=output));continue
            if node['type'].startswith(('LUT','MUX2_LUT')):
                queue.extend((b,depth+1) for p,bits in node['connections'].items() if node['port_directions'][p]=='input' for b in bits)
    for row in candidates.values():row['score']=list(min((w['endpoint']['slack_ns'],w['logic_depth'],w['first_pin']!='SUM',row['length']) for w in row['witnesses']))
    return sorted(candidates.values(),key=lambda r:(r['score'],r['head']))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('source','placement','sta','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--keep-chains',type=int,default=2);p.add_argument('--paths',type=int,default=8);p.add_argument('--frontier',choices=('any','sum','cout'),default='any');a=p.parse_args()
    if not 1<=a.keep_chains<=32 or not 1<=a.paths<=1000:p.error('Invalid selection limits')
    from workspace_budget import reserve
    from audit_ownpr_native_export import audit
    from native_boundary_cec import prove
    reserve(ROOT,50000000);audit(a.sta,a.placement);a.out.mkdir(exist_ok=False)
    original=load_json(a.source);current=load_json(a.placement/'packed.json');proof=prove(original,current,a.out/'source_cec',120)
    mapping=load_json(a.sta/'name_map.json');reverse={v:k for k,v in mapping['instances'].items()};text=read_text(a.sta/'probe.tr')
    section=re.search(r'^3\.1\.1 Setup Paths Table\n(.*?)(?=\n3\.)',text,re.S|re.M)
    if not section:raise ValueError('Missing setup endpoint evidence')
    records=re.findall(r'^\s*\d+\s+([-\d.]+)\s+\S+\s+(own_i_\d+)/(\S+)\s+',section[1],re.M)
    if len(records)!=load_json(a.sta/'handoff_audit.json')['timing']['path_coverage']['setup']['paths']:raise ValueError('Incomplete arithmetic timing feedback')
    endpoints=[dict(instance=reverse[name],pin=pin,slack_ns=float(slack)) for slack,name,pin in records[:a.paths]]
    rows=rank(original,endpoints);eligible=[r for r in rows if r['eligible'] and (a.frontier=='any' or any(w['first_pin']==a.frontier.upper() for w in r['witnesses']))]
    if len(eligible)<a.keep_chains:raise ValueError('Too few calibrated arithmetic candidates')
    result=dict(status='timing_guided_arithmetic_candidate',source_sha256=sha(a.source),feedback_netlist_sha256=sha(a.placement/'packed.json'),feedback_sta_sha256=sha(a.sta/'probe.tr'),
        preserved_chain_heads=[r['head'] for r in eligible[:a.keep_chains]],frontier_policy=a.frontier,ranking=rows,endpoints=endpoints,source_equivalence=proof,physical_gain_verified=False)
    (a.out/'selection.json').write_text(json.dumps(result,indent=2));print(json.dumps(result['preserved_chain_heads'],indent=2))


if __name__=='__main__':main()
