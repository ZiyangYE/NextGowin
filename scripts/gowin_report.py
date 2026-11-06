"""Read native report summaries without treating missing fields as timing pass."""
import re

NUMBER=r'[-+]?\d+(?:\.\d+)?'


def section(text,title):
    match=re.search(r'^'+re.escape(title)+r'\s*$',text,re.M)
    if not match:
        return ''
    tail=text[match.end():]
    end=re.search(r'^3\.\d+(?:\.\d+)?\s',tail,re.M)
    return tail[:end.start()] if end else tail


def summaries(text,hold_uncertainty_ns=0):
    endpoints={a:int(b) for a,b in re.findall(r'<Numbers of (Setup|Hold) Violated Endpoints>:\s*(\d+)',text)}
    if set(endpoints)!={'Setup','Hold'}:
        raise ValueError('Missing setup/hold endpoint summary')
    result=dict(violated_endpoints=endpoints,timing_closed=all(n==0 for n in endpoints.values()))
    freq=re.search(r'probe_clk\s+100\.000\(MHz\)\s+([\d.]+)\(MHz\)',text)
    if freq:
        result['reported_fmax_mhz']=float(freq[1])
    hold=section(text,'3.3.2 Hold Analysis Report')
    values=[float(v) for v in re.findall(r'^\s*'+NUMBER+r'\s+('+NUMBER+r')\s+tUnc\b',hold,re.M)]
    if hold_uncertainty_ns:
        result['report_confirms_hold_uncertainty']=any(abs(v-hold_uncertainty_ns)<.000501 for v in values)
        if not result['report_confirms_hold_uncertainty']:
            raise ValueError('Requested hold uncertainty not found in hold report')
    table=section(text,'3.1.1 Setup Paths Table')
    register_paths=[]
    for line in table.splitlines():
        fields=line.split()
        if len(fields)<9 or not fields[0].isdigit():
            continue
        if re.fullmatch(r'launch\d+/Q',fields[2]) and re.fullmatch(r'capture\d+/D',fields[3]):
            slack,relation=float(fields[1]),float(fields[6])
            register_paths.append(dict(from_node=fields[2],to_node=fields[3],slack_ns=slack,
                required_period_ns=relation-slack,data_delay_ns=float(fields[8])))
    if register_paths:
        worst=max(register_paths,key=lambda p:p['required_period_ns'])
        result['core_setup_worst_reported_path']=worst
        result['core_setup_scope']='Worst register-to-register path appearing in the sorted native setup table; I/O paths excluded'
    return result
