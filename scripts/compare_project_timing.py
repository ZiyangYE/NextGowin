"""Compare independently re-audited physical results with matched settings."""
import argparse
import json
from pathlib import Path
from project_timing import audit


def compare(before,after):
    for field in ['part','dll_sha256','native_tool_version','delay_models','clock_periods_ns','implementation_options']:
        if not before.get(field) or before[field]!=after.get(field):raise ValueError('Physical comparison mismatch: '+field)
    for file in ['project.sdc','project.cst']:
        if before['artifacts_sha256'][file]!=after['artifacts_sha256'][file]:raise ValueError('Physical constraint content differs')
    def metrics(row):
        paths=[p['slack_ns'] for p in row['reported_paths'] if p['analysis']=='setup']
        return dict(timing_closed=row['timing_closed'],violated_endpoints=row['violated_endpoints'],
            per_clock_tns=row['per_clock_tns'],frequency_reports=row['frequency_reports'],
            worst_reported_setup_slack_ns=min(paths) if paths else None,
            physical_resources={k:row['implementation'].get(k) for k in ['lut','alu','registers','bsram']},
            observed_native_elapsed_seconds=row['implementation'].get('native_elapsed_seconds'))
    return dict(scope='One matched physical pair; repeated independent runs are still required for stable QoR/performance claims',
        matched_clock_constraints_and_implementation_options=True,baseline=metrics(before),candidate=metrics(after),
        runtime_scope='Single observed run with concurrent host work; not a controlled runtime speedup benchmark')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('baseline',type=Path);ap.add_argument('candidate',type=Path)
    ap.add_argument('--profile',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    profile=json.loads(args.profile.read_text());before=audit(args.baseline,profile);after=audit(args.candidate,profile)
    result=compare(before,after)
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
