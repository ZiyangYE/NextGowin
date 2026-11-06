"""Audit full-project native clocks, active constraints and physical timing.

Per-clock positive WNS is only reported where present in the sorted path table.
Global violated endpoint and per-clock TNS summaries are checked separately.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from gowin_report import NUMBER


def section(text,title):
    match=re.search(r'^'+re.escape(title)+r'\s*$',text,re.M)
    if not match:return ''
    tail=text[match.end():]
    end=re.search(r'^\d+(?:\.\d+)*\.?\s+[A-Za-z]',tail,re.M)
    return tail[:end.start()] if end else tail


def normalize(command):return ' '.join(command.split())


def implementation(text,profile):
    rows={}
    for line in text.splitlines():
        fields=[f.strip() for f in line.split('|')]
        if len(fields) in (2,3) and fields[0] and fields[0]!='Resources':rows[fields[0]]=fields[1]
    result=dict(native_resource_rows=rows)
    logic=rows.get('--LUT,ALU,ROM16','')
    for label in ['LUT','ALU','ROM16']:
        match=re.search(r'(\d+)\s+'+label+r'\b',logic)
        if match:result[label.lower()]=int(match[1])
    if 'Register' in rows:result['registers']=int(rows['Register'].split('/')[0])
    if 'BSRAM' in rows:
        total=re.fullmatch(r'(\d+)/(\d+)',rows['BSRAM'])
        counts=re.findall(r'(\d+)\s+(?:SDPB|SDPX9B|SPX9B|DPX9B|SP|DP|pROM)\b',rows['BSRAM'])
        if total:
            result['bsram']=int(total[1])
            detail=[int(rows[k]) for k in rows if re.fullmatch(r'--(?:SDPB|SDPX9B|SPX9B|DPX9B|SP|DP|pROM)',k)]
            if detail and sum(detail)!=result['bsram']:raise ValueError('BSRAM total/type breakdown mismatch')
        elif counts:result['bsram']=sum(map(int,counts))
    if 'max_bsram' in profile and ('bsram' not in result or result['bsram']>profile['max_bsram']):
        raise ValueError('Physical BSRAM budget missing/exceeded')
    for name,value in rows.items():
        if re.search(r'unrout|not routed',name,re.I) and value!='0':raise ValueError('Unrouted native resources')
    match=re.search(r'Total Time and Memory Usage: CPU time = (\d+)h (\d+)m ('+NUMBER+r')s, '
                    r'Elapsed time = (\d+)h (\d+)m ('+NUMBER+r')s, Peak memory usage = (\d+)MB',text)
    if match:
        result.update(native_cpu_seconds=3600*int(match[1])+60*int(match[2])+float(match[3]),
            native_elapsed_seconds=3600*int(match[4])+60*int(match[5])+float(match[6]),peak_memory_mb=int(match[7]))
    return result


def required_commands(sdc):
    commands=[]
    for line in sdc.replace('\\\n',' ').splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        name=line.split()[0]
        if name.startswith('report_'):continue
        if name not in ('create_clock','create_generated_clock','set_clock_groups','set_false_path',
                        'set_input_delay','set_output_delay','set_clock_uncertainty','set_max_delay','set_min_delay','set_multicycle_path'):
            raise ValueError('Unsupported SDC audit command: '+name)
        commands.append(normalize(line))
    if not commands:raise ValueError('Empty audited timing constraint set')
    return commands


def parse(text,sdc,profile):
    metadata=dict(re.findall(r'^<([^>]+)>:\s*(.*?)\s*$',text,re.M))
    if metadata.get('Part Number')!=profile['part']:raise ValueError('Wrong/missing native part')
    violated={a:int(b) for a,b in re.findall(r'<Numbers of (Setup|Hold) Violated Endpoints>:\s*(\d+)',text)}
    if set(violated)!={'Setup','Hold'}:raise ValueError('Incomplete violated-endpoint summary')
    def rows(title):
        value=section(text,title)
        if not value:raise ValueError('Missing native section '+title)
        return [line.split() for line in value.splitlines() if line.split() and line.split()[0].isdigit()]
    clocks={}
    for fields in rows('2.2 Clock Summary'):
        if len(fields)<7:raise ValueError('Incomplete clock summary row')
        if fields[1] in clocks:raise ValueError('Duplicate native clock')
        clocks[fields[1]]=float(fields[3])
    expected=profile['clock_periods_ns']
    if set(clocks)!=set(expected) or any(abs(clocks[k]-expected[k])>.000501 for k in expected):
        raise ValueError('Physical clock set/period differs from expected profile: '+str(clocks))
    freq={}
    for match in re.finditer(r'^\s*\d+\s+(\S+)\s+('+NUMBER+r')\(MHz\)\s+('+NUMBER+r')\(MHz\)',
                            section(text,'2.3 Max Frequency Summary'),re.M):
        freq[match[1]]=dict(constraint_mhz=float(match[2]),reported_fmax_mhz=float(match[3]))
    tns=[]
    tns_section=section(text,'2.4 Total Negative Slack Summary')
    for line in tns_section.splitlines():
        fields=line.split()
        if len(fields)==4 and fields[1].lower() in ('setup','hold','recovery','removal'):
            tns.append(dict(clock=fields[0],analysis=fields[1].lower(),tns_ns=float(fields[2]),violated_endpoints=int(fields[3])))
    required={(clock,analysis) for clock in expected for analysis in ('setup','hold')}
    if not required<={(r['clock'],r['analysis']) for r in tns}:raise ValueError('Missing per-clock setup/hold TNS')
    paths=[]
    for number,analysis in enumerate(['setup','hold','recovery','removal'],1):
        for fields in rows(f'3.1.{number} {analysis.title()} Paths Table'):
            if len(fields)!=9:raise ValueError('Unexpected detailed path format')
            paths.append(dict(analysis=analysis,slack_ns=float(fields[1]),source=fields[2],destination=fields[3],
                launch_clock=fields[4],capture_clock=fields[5],relationship_ns=float(fields[6]),
                clock_skew_ns=float(fields[7]),data_delay_ns=float(fields[8])))
    pulse=[]
    for fields in rows('3.2 Minimum Pulse Width Table'):
        if len(fields)<7:raise ValueError('Unexpected pulse-width row')
        pulse.append(dict(slack_ns=float(fields[1]),actual_ns=float(fields[2]),required_ns=float(fields[3]),
                          clock=fields[-2],object=fields[-1]))
    constraint_section=section(text,'3.8 Timing Constraints Report')
    commands=[normalize(m[1]) for m in re.finditer(r'^\s*TC_\w+\s+Actived\s+(.+?)\s*$',constraint_section,re.M)]
    missing=[c for c in required_commands(sdc) if c not in commands]
    if missing:raise ValueError('Timing constraints not active: '+repr(missing))
    failures=[dict(kind='endpoint_summary',analysis=k,violated_endpoints=v) for k,v in violated.items() if v]
    failures.extend(dict(kind='per_clock_tns',**r) for r in tns if r['tns_ns']<0 or r['violated_endpoints'])
    failures.extend(dict(kind='reported_path',**r) for r in paths if r['slack_ns']<0)
    failures.extend(dict(kind='pulse_width',**r) for r in pulse if r['slack_ns']<0)
    worst={}
    for row in paths:
        key=row['capture_clock']+'/'+row['analysis']
        worst[key]=min(worst.get(key,float('inf')),row['slack_ns'])
    return dict(part=profile['part'],native_tool_version=metadata.get('Tool Version'),
        delay_models=dict(setup=metadata.get('Setup Delay Model'),hold=metadata.get('Hold Delay Model')),
        clock_periods_ns=clocks,frequency_reports=freq,
        violated_endpoints=violated,per_clock_tns=tns,active_sdc_commands=commands,
        worst_reported_slack_by_capture_clock=worst,reported_paths=paths,pulse_width=pulse,
        timing_closed=not failures,violations=failures,
        scope='Native endpoint/TNS totals plus reported setup/hold/recovery/removal/pulse paths. Positive slack values cover reported paths only; no hardware acceptance.')


def audit(directory,profile):
    manifest=json.loads((directory/'manifest.json').read_text())
    if manifest.get('returncode')!=0:raise ValueError('P&R is not terminal with success return code')
    def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
    if not manifest.get('netlist_sha256_before')==manifest.get('netlist_sha256_after')==sha(directory/'mapped.vg'):
        raise ValueError('P&R input changed or hash evidence incomplete')
    constraint_files=[('project.sdc','sdc_sha256')]
    if manifest.get('cst_sha256') is not None:constraint_files.append(('project.cst','cst_sha256'))
    for file,field in constraint_files:
        if sha(directory/file)!=manifest[field]:raise ValueError('Constraint snapshot changed')
    log=(directory/'pnr.log').read_text(errors='replace')
    routing=(directory/'mapped.rpt.txt').read_text(errors='replace')
    if 'Placement and routing completed' not in log or 'Generate output files:' not in routing:
        raise ValueError('Native implementation completion evidence missing')
    if re.search(r'ERROR\s*\(|PR0004',log+'\n'+routing,re.I):raise ValueError('Native implementation reported an error')
    if 'CT1135' in log:raise ValueError('A physical constraint object was not found')
    files=['mapped.vg','mapped.vo','mapped.tr','mapped.rpt.txt','project.sdc','cmd.do']
    if manifest.get('cst_sha256') is not None:files.append('project.cst')
    bitstream_requested='-bit' in (directory/'cmd.do').read_text().splitlines()
    if bitstream_requested:
        bits=list(directory.glob('ao_*.fs')) if manifest.get('gao_id') else [directory/'mapped.fs']
        if not bits:raise ValueError('Requested configuration output missing')
        files.extend(p.name for p in bits)
    if manifest.get('rao_sha256'):
        if sha(directory/'debug.rao')!=manifest['rao_sha256']:raise ValueError('RTL GAO configuration changed')
        files.append('debug.rao')
    if any(not (directory/p).is_file() for p in files):raise ValueError('Missing native output')
    source_time=(directory/'mapped.vg').stat().st_mtime
    if any((directory/p).stat().st_mtime<source_time for p in ['mapped.vo','mapped.tr','mapped.rpt.txt']):
        raise ValueError('Stale native output')
    result=parse((directory/'mapped.tr').read_text(errors='replace'),(directory/'project.sdc').read_text(),profile)
    result.update(pnr_artifacts_valid=True,source_manifest_sha256=sha(directory/'manifest.json'),
        artifacts_sha256={p:sha(directory/p) for p in files},runner_recorded_status=manifest['status'],
        bitstream_requested=bitstream_requested)
    result['dll_sha256']=manifest['dll_sha256']
    result['implementation_options']=[line.strip() for line in (directory/'cmd.do').read_text().splitlines()
        if line.strip() and line.split()[0] not in ('-d','-sdc','-cst','-o')]
    result['implementation']=implementation(routing,profile)
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('directory',type=Path)
    ap.add_argument('--profile',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args();result=audit(args.directory,json.loads(args.profile.read_text()))
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('reported_paths','pulse_width','artifacts_sha256')},indent=2))
    if not result['timing_closed']:raise SystemExit(2)


if __name__=='__main__':main()
