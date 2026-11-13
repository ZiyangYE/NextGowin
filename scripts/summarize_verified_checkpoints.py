"""Build a timing-only index from checked checkpoints, not claimed full support."""
import argparse,json,re
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha,read_text
from run_ownpr_pin_closure import metric
from workspace_budget import reserve

def summarize(reports):
    rows=[];cst=None
    for report in reports:
        row=load_json(report)['designs'][0]
        for name,digest in row['selected_source_sha256'].items():
            if sha(ROOT/name)!=digest:raise ValueError('Checkpoint changed: '+name)
        sta=ROOT/row['selected']['sta'];observed=metric(sta)
        if observed!=row['metrics'] or not observed['native_constraints_satisfied'] or not observed['other_checks_passed'] or observed['setup_violations'] or observed['hold_violations']:raise ValueError('Checkpoint not fully timing-qualified')
        current=sha(ROOT/row['constraints']['cst'])
        if cst is not None and current!=cst:raise ValueError('Incomparable fixed package constraints')
        cst=current;sdc=ROOT/row['constraints']['sdc'];match=re.search(r'create_clock\s+-name\s+clk\s+-period\s+([\d.]+)',read_text(sdc))
        if not match:raise ValueError('Unsupported clock summary')
        native=load_json(sta/'manifest.json');audit=load_json(sta/'handoff_audit.json')
        if native['cst_sha256']!=cst or native['source_sdc_sha256']!=sha(sdc):raise ValueError('Report constraint provenance differs')
        rows.append(dict(report=str(report),report_sha256=sha(report),qualified_mhz=1000/float(match[1]),metrics=observed,bitstream_generated=native['bitstream_generated'],full_bitstream_verified=audit['full_bitstream_verified'],device_programmed=audit['device_programmed']))
    return dict(status='checked_timing_checkpoint_index',goal_complete=False,fixed_cst_sha256=cst,scope='Same-device fixed-pin timing qualification only; does not establish general superiority, arbitrary primitive support, or hardware function',checkpoints=sorted(rows,key=lambda r:r['qualified_mhz']))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--reports',nargs='+',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();reserve(ROOT,1_000_000);result=summarize(a.reports)
    with a.out.open('x') as stream:json.dump(result,stream,indent=2)
    print([(round(r['qualified_mhz'],3),r['metrics']['setup_wns_ns']) for r in result['checkpoints']])
