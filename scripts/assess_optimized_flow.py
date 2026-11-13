"""Assess a finished flow against physical timing and one-hour stage totals."""
import argparse
import json
from pathlib import Path
import re
from run_abc_reproduction import ROOT,sha


def assess(folder):
    folder=folder.resolve()
    flow=json.loads((folder/'manifest.json').read_text())
    stages=flow['stages'];pnr=next(s['result'] for s in stages if s['name']=='pnr_bitstream')
    native=pnr['native'];timing=pnr['timing']
    if flow['status']!='complete' or native['status']!='complete':
        raise ValueError('Flow execution is not complete; do not assess an unfinished bitstream')
    checks={}
    for name,record in native['artifacts'].items():
        path=folder/'06_pnr'/name
        checks['artifact:'+name]=path.is_file() and sha(path)==record['sha256'] and path.stat().st_size==record['bytes']
    checks['fresh_bitstream_present']=any(n.endswith('.fs') for n in native['artifacts'])
    checks['input_netlist_unchanged']=native['netlist_sha256_before']==native['netlist_sha256_after']==sha(folder/'06_pnr/mapped.vg')==sha(folder/'05_netlist/mapped.vg')
    for kind in ('sdc','cst'):
        configured=flow['config'].get(kind)
        if configured:
            source=Path(configured)
            if not source.is_absolute():source=ROOT/source
            checks['original_'+kind+'_unchanged']=sha(source)==native[kind+'_sha256']==sha(folder/'06_pnr'/('project.'+kind))
    profile=flow['config'].get('physical_profile')
    if profile:
        path=Path(profile)
        if not path.is_absolute():path=ROOT/path
        expected=json.loads(path.read_text())
        checks['all_required_clock_periods']=timing['clock_periods_ns']==expected['clock_periods_ns']
        checks['correct_part']=timing['part']==expected['part']
    report=(folder/'06_pnr/mapped.rpt.txt').read_text(errors='replace')
    def capacity(label):
        match=re.search(r'^\s*'+re.escape(label)+r'\s*\|\s*(\d+)/(\d+)\s*\|',report,re.M)
        if not match:raise ValueError('Missing native resource count: '+label)
        return dict(used=int(match[1]),capacity=int(match[2]))
    resources={label:capacity(label) for label in ('Logic','Register','CLS','BSRAM','DSP','LW')}
    if profile and 'max_bsram' in expected:
        checks['bsram_within_project_budget']=resources['BSRAM']['used']<=expected['max_bsram']
    distributed=re.search(r'^\s*--SSRAM\(RAM16\)\s*\|\s*(\d+)\s*\|',report,re.M)
    resources['distributed_ram_instances']=int(distributed[1]) if distributed else 0
    preflight=json.loads((folder/'05_netlist/resource_preflight.json').read_text())
    resources['dsp_primitive_instances']=sum(preflight['leaf_cells'].get(k,0) for k in ('MULT12X12','MULTALU27X18','MULT27X36'))
    total=sum(s['wall_seconds'] for s in stages)
    checks['recorded_stage_total_matches']=abs(total-flow['total_stage_wall_seconds'])<0.001
    integrity=all(checks.values())
    pnr_time_ok=native['pnr_wall_seconds']<=3600
    total_time_ok=total<=3600
    timing_ok=flow['timing_closed'] and all(n==0 for n in timing['violated_endpoints'].values())
    passed=integrity and pnr_time_ok and total_time_ok and timing_ok
    return dict(status='passed' if passed else 'bitstream_generated_requirements_failed',
        integrity_verified=integrity,checks=checks,pnr_within_3600_seconds=pnr_time_ok,
        total_stage_time_within_3600_seconds=total_time_ok,pnr_wall_seconds=native['pnr_wall_seconds'],
        total_stage_wall_seconds=total,timing_closed=timing_ok,
        frequency_reports=timing['frequency_reports'],violated_endpoints=timing['violated_endpoints'],
        resources=resources,clock_periods_ns=timing['clock_periods_ns'],
        warnings=re.findall(r'^WARN\s*\(.*$',(folder/'06_pnr/pnr.log').read_text(errors='replace'),re.M),
        bitstreams={n:r for n,r in native['artifacts'].items() if n.endswith('.fs')},board_programmed=flow['board_programmed'],
        scope='Sum of accepted stage durations, including retained upstream stages when resumed; excludes idle gaps and abandoned attempts. Timing is native reported timing, not board validation.')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('flow',type=Path);ap.add_argument('--out',type=Path)
    args=ap.parse_args();record=assess(args.flow)
    target=args.out or args.flow/'acceptance.json'
    target.write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record,indent=2))
    if record['status']!='passed':raise SystemExit(1)


if __name__=='__main__':main()
