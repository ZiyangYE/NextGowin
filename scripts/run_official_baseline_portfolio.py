"""Common, predeclared official implementation portfolio; never own P&R.

Keep full reports, native snapshots, encoded images and source provenance.
New terminal PR serialization caches are inventoried then discarded; reproducing
one requires rerunning the recorded official command, not loading a retained PR.
"""
import argparse,json,subprocess,sys,time,shutil
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha,compact,compact_html,compact_json
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree


def terminal_storage(folder):
    folder=folder.resolve()
    if not folder.is_relative_to(ROOT/'results'):raise ValueError('Result folder outside workspace')
    record=dict(status='planned',scope='Only this newly generated terminal candidate; PR is an unused serialization cache, not a certificate input. Rebuild from recorded sources/command for native PR replay.',
        logical_evidence_retained=True,full_pr_serialization_retained=False,operations=[])
    path=folder/'storage_inventory.json';path.write_text(json.dumps(record,indent=2))
    pr=folder/'probe.pr'
    if pr.exists():
        if pr.is_symlink() or not pr.resolve().is_relative_to(folder):raise ValueError('Unexpected PR cache target')
        row=dict(action='remove_rebuildable_terminal_serialization',path=pr.name,bytes=pr.stat().st_size,sha256=sha(pr))
        record['operations'].append(row);path.write_text(json.dumps(record,indent=2));pr.unlink()
    files=[(p,compact_html) for p in folder.glob('*.html') if p.stat().st_size>100000]
    files += [(folder/'probe.fs',compact),(folder/'physical_before.json',compact_json)]
    existing=[(p,fn) for p,fn in files if p.exists()]
    reserve(ROOT,max((p.stat().st_size for p,fn in existing),default=0)+1000000)
    for p,fn in existing:record['operations'].append(dict(action='lossless_compaction',**fn(p)))
    record['status']='complete';path.write_text(json.dumps(record,indent=2))
    return dict(inventory=str(path.relative_to(ROOT)),sha256=sha(path),pr_replay_requires_rebuild=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('catalog','budget','out'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();catalog=load_json(a.catalog);budget=load_json(a.budget);out=a.out.resolve()
    reserve(ROOT,20000000);out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'runner_source.py.txt')
    result=dict(status='running',role='official_synthesis_official_pnr_portfolio',own_pnr_result=False,baseline_frozen=False,
        catalog_sha256=sha(a.catalog),original_budget=budget,common_portfolio=catalog['portfolio'],cases=[])
    def save():
        result['wall_seconds']=time.time()-budget['start_epoch'];(out/'manifest.json').write_text(json.dumps(result,indent=2))
    save()
    # Round robin over designs: apply the exact same options and period set.
    for policy in catalog['portfolio']['io_register_policies']:
        for period in catalog['portfolio']['implementation_periods_ns']:
            for item in catalog['designs']:
                if time.time()+540>budget['deadline_epoch']:
                    result['status']='portfolio_incomplete_original_budget';save();return
                source=ROOT/item['synthesis'];sm=load_json(source/'manifest.json')
                if sm['status']!='complete' or sm['netlist_sha256']!=sha(source/'mapped.vg'):raise ValueError('Official synthesis changed')
                for f,h in sm['rtl_input_sha256'].items():
                    if sha(Path(f))!=h:raise ValueError('Original RTL input changed')
                name=f"{item['design']}_{policy}_{period:g}ns";folder=out/name
                row=dict(design=item['design'],policy=policy,implementation_period_ns=period,folder=str(folder.relative_to(ROOT)),
                    official_synthesis_manifest_sha256=sha(source/'manifest.json'),mapped_sha256=sha(source/'mapped.vg'),status='running')
                result['cases'].append(row);save()
                cmd=[sys.executable,str(ROOT/'experiments/native_fixed_layout_sta.py'),'--netlist',str(source/'mapped.vg'),'--cst',str(ROOT/item['cst']),
                    '--sdc',str(ROOT/item['sdc']),'--budget',str(a.budget.resolve()),'--out',str(folder),'--initial-period',str(period),
                    '--io-register-policy',policy,'--restore-native-inverters','--normalization-engine','recorded_controls','--bitstream','--capture-connectivity','--capture-ioreg']
                row['command']=cmd;save()
                with (out/(name+'.log')).open('x') as log:
                    proc=subprocess.Popen(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    try:code=proc.wait(timeout=min(520,budget['deadline_epoch']-time.time()-120))
                    except subprocess.TimeoutExpired:stop_owned_process_tree(proc);code=-1
                row['returncode']=code
                f=folder/'reload_result.json';cal=load_json(f) if f.exists() else {}
                row.update(status=cal.get('status','failed'),baseline_frozen=False)
                if code==0 and cal.get('status')=='fixed_layout_sta_calibrated':
                    last=cal['phases'][-1];row.update(reported_fmax_mhz=cal['final_reported_fmax_mhz'],hold_passed=cal['final_hold_passed'],
                        evaluation_mhz=cal['sta_evaluation_mhz'],worst_slack=last['worst_reported_slack'],
                        violated_endpoints=last['violated_endpoints'],reload_sha256=sha(f),
                        scope='Native four-class/inventory/period calibration only; full acceptance and source proof remain separate')
                if folder.exists():row['storage']=terminal_storage(folder)
                save()
    result.update(status='portfolio_completed_unfrozen',workspace=reserve(ROOT,0));save()
    print(json.dumps(dict(status=result['status'],cases=len(result['cases']),wall_seconds=result['wall_seconds'])))


if __name__=='__main__':main()
