"""Clock feedback using competing setup arrivals and conservative hold bounds.

Single positive primary clock, fixed data placement/routes and zero input delay.
Reported transitions anchor required times; native complete STA remains mandatory.
"""
from ownpr_image_storage import read_text as read_artifact_text
import argparse,copy,json,math,re
from pathlib import Path
from extract_timing import ROW
from ownpr_arrival_graph import build,propagate
from ownpr_place import sha
from ownpr_image_storage import load_json
from ownpr_timing_numbers import negative_slack


def clock_rows(rows):
    result={}
    for row in rows:
        node=tuple(row['node']);v=row['delays_by_selector']
        if node in result or len(v)!=3 or any(len(x)!=4 for x in v) or any(not math.isfinite(x) or x<0 for xs in v for x in xs):raise ValueError('Invalid clock feedback vector')
        result[node]=v
    return result


def anchored_slack(value,delta):
    answer=value+delta
    return -0.0 if answer==0 and delta==0 and negative_slack(value) else answer


def minimum(values):return min(values,key=lambda x:(x,not negative_slack(x)))


def parse_constraints(text,captures,coverage):
    clocks={r['native_name']:r['nodes'][0] for r in captures if r['pin']=='CLK' and len(r['nodes'])==1}
    aliases={r['native_name']:r['instance'] for r in captures}
    result={};domain=None
    for number,kind in enumerate(('setup','hold','recovery','removal'),1):
        section=re.search(r'^3\.3\.'+str(number)+r' .*?\n(.*?)(?=^3\.|\Z)',text,re.M|re.S)
        if not section:raise ValueError('Missing clock feedback section '+kind)
        blocks=re.split(r'^\s*Path(\d+)\s*$',section[1],flags=re.M);items=[]
        def clock(trace):
            found=[]
            for line in trace.splitlines():
                m=ROW.match(line)
                if not m:continue
                arrival,delay,typ,rf,_,_,label=m.groups();alias,_,pin=label.rpartition('/')
                if typ!='tNET' or pin!='CLK':continue
                if rf!='RR' or alias not in clocks:raise ValueError('Unsupported clock edge/domain in feedback')
                query=clocks[alias];selector,index=(1,2) if kind in ('setup','recovery') else (2,0)
                if abs(float(delay)-query['wire_delays'][selector][index])>.00051:raise ValueError('Clock report/query corner mismatch')
                found.append(query['node'])
            if len(found)>1:raise ValueError('Multiple clock boundaries in a timing trace')
            return found[0] if found else None
        for i in range(1,len(blocks),2):
            body=blocks[i+1];slack=re.search(r'^Slack\s*:\s*([-\d.]+)',body,re.M)
            arrival=re.search(r'Data Arrival Path:\n(.*?)(?=\nData Required Path:)',body,re.S)
            required=re.search(r'Data Required Path:\n(.*?)(?=\nPath Statistics:|\Z)',body,re.S)
            if not slack or not arrival or not required:raise ValueError('Incomplete clock constraint trace')
            # Both launch/latch references must stay the audited positive base clock.
            references=re.findall(r'^(?:Launch|Latch) Clk\s*:\s*(\S+)',body,re.M)
            if len(references)!=2 or references[0]!=references[1] or not references[0].endswith(':[R]'):raise ValueError('Unsupported clock relationship')
            if domain is not None and domain!=references[0]:raise ValueError('Multiple clock domains in feedback')
            domain=references[0]
            trace=[m.groups() for line in arrival[1].splitlines() if (m:=ROW.match(line))]
            endpoint=None
            if trace:
                alias,_,pin=trace[-1][-1].rpartition('/')
                if alias in aliases:endpoint=aliases[alias]+'/'+pin
            items.append(dict(path=int(blocks[i]),slack_ns=float(slack[1]),launch=clock(arrival[1]),capture=clock(required[1]),endpoint=endpoint))
        expected=coverage.get(kind)
        if expected is None:
            # Synchronous-only designs have explicit empty async report
            # sections but the scalar coverage audit has no async keys.
            if kind not in ('recovery','removal') or items:raise ValueError('Clock feedback report coverage changed')
        elif len(items)!=expected['paths']:raise ValueError('Clock feedback report coverage changed')
        result[kind]=items
    return result


def external_launch_proof(design,endpoint):
    """Structural superset of all launches, never just the reported path.

    Only ordinary combinational LUT/mux fanin ending at non-clock primary
    IBUFs is admitted. State, hard cells, loops and ambiguous drivers retain
    the original global conservative bound.
    """
    if not endpoint:return None
    cells=design['modules']['top']['cells'];name,_,pin=endpoint.rpartition('/')
    if name not in cells or cells[name]['port_directions'].get(pin)!='input':return None
    drivers={};clocks={b for c in cells.values() for p,bs in c['connections'].items() if p in ('CLK','CLKA','CLKB') for b in bs}
    for n,c in cells.items():
        for p,bs in c['connections'].items():
            if c['port_directions'][p]=='output':
                for b in bs:drivers.setdefault(b,[]).append(n)
    roots=set();done=set();active=set()
    def visit(bit):
        if bit in ('0','1'):return True
        if bit in clocks:return False
        if bit in done:return True
        if bit in active or len(drivers.get(bit,[]))!=1:return False
        n=drivers[bit][0];c=cells[n];typ=c['type']
        if typ in ('GND','VCC'):done.add(bit);return True
        if typ=='IBUF':
            # Verify this is actually a primary top-level input.
            if not any(p['direction']=='input' and c['connections']['I']==[b] for p in design['modules']['top']['ports'].values() for b in p['bits']):return False
            roots.add(n);done.add(bit);return True
        if typ not in ('LUT1','LUT2','LUT3','LUT4','MUX2_LUT5','MUX2_LUT6','MUX2_LUT7'):return False
        active.add(bit)
        ok=all(visit(b) for p,bs in c['connections'].items() if c['port_directions'][p]=='input' for b in bs)
        active.remove(bit)
        if ok:done.add(bit)
        return ok
    if all(visit(b) for b in cells[name]['connections'][pin]) and roots:
        return dict(endpoint=endpoint,primary_ibufs=sorted(roots),structural_fanin_only=True)
    return None


def compile_model(placement,sta):
    placement,sta=Path(placement),Path(sta);graph,labels,info=build(placement,sta);values,_,_=propagate(**graph)
    error=max(abs(values[n][tr]-r['reported_arrival_ns']) for (n,tr),r in labels.items())
    if error>.002:raise ValueError('Clock feedback baseline arrivals differ by more than 2 ps')
    captures=load_json(sta/'sta_endpoint_audit.json');audit=json.loads((sta/'handoff_audit.json').read_text())
    constraints=parse_constraints(read_artifact_text(sta/'probe.tr'),captures,audit['timing']['path_coverage'])
    design=load_json(placement/'packed.json')
    for row in constraints['hold']:
        proof=external_launch_proof(design,row.get('endpoint'))
        if proof:
            if row['launch'] is not None:raise ValueError('External-only structural proof conflicts with reported launch')
            row['external_launch_proof']=proof
    clock_data={};ff_clocks={}
    for row in captures:
        if row['pin']!='CLK':continue
        if len(row['nodes'])!=1:raise ValueError('Ambiguous clock endpoint')
        node=row['nodes'][0];key=tuple(node['node']);v=node['wire_delays']
        if key in clock_data and clock_data[key]!=v:raise ValueError('Shared clock query mismatch')
        clock_data[key]=v;seed=row['instance']+'/Q'
        if seed not in graph['seeds']:raise ValueError('Clocked state lacks a modeled Q boundary')
        ff_clocks[seed]=list(key)
    by_path={r['path']:r for r in constraints['setup']}
    setup=[dict(endpoint=n,transition=tr,**reference,capture=by_path[reference['path']]['capture']) for (n,tr),reference in labels.items()]
    info['source_sha256'].update({str((sta/n).resolve()):sha(sta/n) for n in ('name_map.json','clock_tree.json')})
    return dict(status='compiled_clock_slack_feedback',graph=graph,setup=setup,ff_clocks=ff_clocks,
        clocks=[dict(node=list(n),delays_by_selector=v) for n,v in sorted(clock_data.items())],constraints=constraints,
        baseline_arrival_error_ns=error,source_sha256=info['source_sha256'],
        timing_signoff=False,scope='Competing slow setup arrivals at reported transitions; conservative hold/removal and recovery bounds with fixed data arcs. Single positive clock only; full native STA required.')


class ClockSlackModel:
    def __init__(self,data):
        self.data=data;self.base=clock_rows(data['clocks']);self.graph=data['graph'];self.values=propagate(**self.graph)[0]

    def priorities(self):
        result={}
        for row in self.data['constraints']['setup']:
            for key in ('launch','capture'):
                if row[key] is not None:
                    node=tuple(row[key]);result[node]=min(result.get(node,math.inf),row['slack_ns'])
        return result

    def evaluate(self,rows):
        current=clock_rows(rows)
        if set(current)!=set(self.base):raise ValueError('Clock feedback endpoints changed')
        slow={n:current[n][1][2]-v[1][2] for n,v in self.base.items()}
        fast={n:current[n][2][0]-v[2][0] for n,v in self.base.items()}
        seeds=copy.deepcopy(self.graph['seeds'])
        for seed,node in self.data['ff_clocks'].items():seeds[seed]=[v+slow[tuple(node)] for v in seeds[seed]]
        changed=propagate(self.graph['nodes'],self.graph['edges'],seeds)[0];slacks=[]
        for r in self.data['setup']:
            n,tr=r['endpoint'],r['transition'];arrival_delta=changed[n][tr]-self.values[n][tr]
            if not math.isfinite(arrival_delta):raise ValueError('Clock feedback lost a setup endpoint')
            delta=slow[tuple(r['capture'])] if r['capture'] is not None else 0.
            slacks.append(dict(endpoint=n,transition=tr,slack_ns=anchored_slack(r['reported_slack_ns'],delta-arrival_delta)))
        bounds={};small=min([0.,*fast.values()]);large=max([0.,*slow.values()])
        for kind in ('hold','removal','recovery'):
            values=[]
            for r in self.data['constraints'][kind]:
                node=tuple(r['capture']) if r['capture'] is not None else None
                # Any unreported launch can become critical. Bound its clock
                # delta over all clocked boundaries, plus external inputs.
                launch_min=0. if kind=='hold' and r.get('external_launch_proof') else small
                change=(launch_min-(fast[node] if node is not None else 0.) if kind!='recovery' else -large+(slow[node] if node is not None else 0.))
                v=anchored_slack(r['slack_ns'],change)
                values.append(v)
            bounds[kind]=minimum(values) if values else None
        return dict(setup_wns_ns=minimum(r['slack_ns'] for r in slacks),setup_violations=sum(negative_slack(r['slack_ns']) for r in slacks),
            total_negative_setup_slack_ns=sum(min(0.,r['slack_ns']) for r in slacks),worst_endpoint=min(slacks,key=lambda r:r['slack_ns']),
            conservative_bounds_ns=bounds,conservative_checks_passed=all(v is None or not negative_slack(v) for v in bounds.values()),timing_signoff=False)


def score(metrics):
    if not metrics['conservative_checks_passed']:return None
    # Native report slacks have 1 ps resolution. Do not spend an export on
    # sub-ps differences produced only by float32 accumulation order.
    return (round(metrics['setup_wns_ns'],3),-metrics['setup_violations'],round(metrics['total_negative_setup_slack_ns'],3))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--placement',type=Path,required=True);p.add_argument('--sta',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from ownpr_device import ROOT
    from workspace_budget import reserve
    reserve(ROOT,5_000_000);data=compile_model(a.placement,a.sta);a.out.write_text(json.dumps(data,indent=2));print(json.dumps(ClockSlackModel(data).evaluate(data['clocks']),indent=2))
