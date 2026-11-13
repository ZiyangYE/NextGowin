"""Cross-check native feedback selectors against complete exported STA tables."""
import argparse,collections,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,read_text,sha
from extract_timing import ROW
def analyze(folder):
    folder=Path(folder);m=load_json(folder/'manifest.json');assert m['status']=='captured' and not m['vendor_routing_executed']
    rows=load_json(folder/'pin_feedback.json')['pins'];report=read_text(folder/'report_before.tr');after=read_text(folder/'report_after.tr')
    tables={}
    for i,kind in ((1,'Setup'),(2,'Hold')):
        s=re.search(r'^3\.1\.'+str(i)+' '+kind+r' Paths Table\n(.*?)(?=\n3\.)',report,re.M|re.S)[1]
        tables[kind]={end:float(slack) for slack,end in re.findall(r'^\s*\d+\s+([-\d.]+)\s+\S+\s+(\S+)\s+',s,re.M)}
    pins={r['native_name']+'/'+r['pin']:r for r in rows};matches={}
    for kind,table in tables.items():
        matches[kind]={}
        for selector in ('cached_before','selected_0','selected_1','cached_after_report'):
            delta=[(abs(pins[p][selector]-v),p,v,pins[p][selector]) for p,v in table.items() if p in pins and isinstance(pins[p][selector],(float,int))]
            mismatches=sorted(delta,reverse=True)
            matches[kind][selector]=dict(total_reported=len(table),queried=len(delta),matched_1ps=sum(d<=.0011 for d,*_ in delta),worst_examples=mismatches[:8])
    body=lambda t:t.split('\n2. Timing Summaries\n',1)[1]
    mutation={k:sum(r[k]!=r['cached_before'] for r in rows) for k in ('cached_after_0','cached_after_1','cached_after_report')}
    internal={}
    for i,kind,mode,combo in ((1,'Setup',1,'11'),(2,'Hold',0,'01')):
        section=re.search(r'^3\.3\.'+str(i)+' '+kind+r' Analysis Report\n(.*?)(?=^3\.\d|\Z)',report,re.M|re.S)[1]
        observations=collections.Counter();covered=set();bad=[]
        for block in re.split(r'^\s*Path\d+\s*$',section,flags=re.M)[1:]:
            slack=float(re.search(r'^Slack\s*:\s*([-\d.]+)',block,re.M)[1])
            arrival=block.split('Data Arrival Path:')[1].split('Data Required Path:')[0]
            data=False
            for line in arrival.splitlines():
                match=ROW.match(line)
                if not match:continue
                at,delay,typ,rf,fanout,loc,node=match.groups()
                if typ=='tC2Q':data=True
                if node in pins and pins[node]['direction']=='Out' and pins[node]['instance'].startswith('ng_pad_'):data=True
                if not data or node not in pins:continue
                row=pins[node];value=row['selected_'+str(mode)];observations['data_rows']+=1
                if not isinstance(value,(float,int)) or abs(value)>=1e9:observations['sentinel_rows']+=1;continue
                if value>slack+.0011:bad.append(dict(node=node,slack=slack,query=value,reason='pin_slack_exceeds_path_slack'))
                if abs(value-slack)<=.0011:
                    actual=row['arrival_after_'+str(mode)][combo];observations['worst_through_pin_rows']+=1;covered.add(node)
                    if isinstance(actual,(float,int)) and abs(actual-float(at))<=.0011:observations['arrival_matched_1ps']+=1
                    else:observations['arrival_not_matching_report_row']+=1
        # Multiple near-equal paths/transition choices can share the rounded slack;
        # do not infer both transition arrivals from this single aggregate getter.
        table_pattern=r'^3\.1\.'+str(i)+' '+kind+r' Paths Table\n(.*?)(?=\n3\.)'
        internal[kind]=dict(counts=observations,distinct_worst_through_pins=len(covered),bad_slack_examples=bad[:12],bad_slack_rows=len(bad),
          report_table_unchanged=re.search(table_pattern,report,re.M|re.S)[1]==re.search(table_pattern,after,re.M|re.S)[1])
    result=dict(status='analyzed',source_sha256={n:sha(folder/n) for n in ('pin_feedback.json','report_before.tr','report_after.tr')},pins=len(rows),vertices=sum(r['has_vertex'] for r in rows),endpoint_matches=matches,
        cache_changed_from_before=mutation,report_body_unchanged=body(report)==body(after),
        unsupported_values=collections.Counter(str(r['selected_0']) for r in rows if not isinstance(r['selected_0'],(float,int)) or abs(r['selected_0'])>=1e9),internal_report_crosscheck=internal,
        used_for_optimization=False,scope='Endpoint selector validation only; internal arrival/required semantics require additional checks.')
    return result
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--capture',type=Path,required=True);a=p.parse_args();r=analyze(a.capture);(a.capture/'analysis.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2))
