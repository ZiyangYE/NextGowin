"""Serial, deadline-bounded own-synthesis candidates with native QoR selection.

All candidates start at one own resource-inference checkpoint. Vendor synthesis
is never a seed. Actual final JSON CEC, original SDC/CST, native routing and
setup/hold audit are mandatory. A valid output is not a superiority claim.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from flow_budget import Budget,activate
from map_resource_json import map_design
from nextgowin import materialize
from project_timing import audit
from run_abc_reproduction import ROOT,sha


def quality(report):
    violations=report['violated_endpoints'];frequencies=report['frequency_reports']
    ratio=min((r['reported_fmax_mhz']/r['constraint_mhz'] for r in frequencies.values()),default=0.0)
    implementation=report['implementation']
    return (int(report['timing_closed']),-violations['Hold'],ratio,-violations['Setup'],
            -sum(implementation.get(k,0) for k in ('lut','alu')))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--config',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--total-timeout',type=int,default=3600)
    ap.add_argument('--recipes',nargs='+',choices=['lut4','wide','wide_no_recovery','lut4_local_wide','lut4_pins','wide_pins','local_wide_pins'],default=['lut4','wide','wide_no_recovery'])
    args=ap.parse_args();budget=Budget(args.total_timeout);activate(budget)
    config=json.loads(args.config.read_text());out=args.out.resolve();out.mkdir(parents=True)
    def path(key):
        value=Path(config[key]);return value if value.is_absolute() else ROOT/value
    if not config.get('physical_profile'):raise ValueError('A checked physical clock/part profile is required')
    profile=json.loads(path('physical_profile').read_text())
    config.update(strategy='arch_delay',architecture_recipe='wide',total_timeout=args.total_timeout)
    cfg=out/'shared_config.json';cfg.write_text(json.dumps(config,indent=2)+'\n')
    method_paths=sorted((ROOT/'scripts').glob('*.py'))+[ROOT/'configs/gw5a_arch_cost_v2.json',
        ROOT/'results/timing/gowin138_model.json',ROOT/'results/timing/gowin138_model_ff_checked.json']
    result=dict(schema='native-feedback-synthesis-v1',status='running',recipes=list(dict.fromkeys(args.recipes)),
        total_timeout_seconds=args.total_timeout,source_config_sha256=sha(args.config),candidates=[],
        method_sha256={p.relative_to(ROOT).as_posix():sha(p) for p in method_paths},
        vendor_synthesis_seed=False,superiority_established=False,
        policy='Serial own candidates; final actual JSON CEC and native setup/hold/resource audit. One shared wall budget; all failures retained.')
    snapshot=out/'method_source'
    for name,digest in result['method_sha256'].items():
        target=snapshot/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
        if sha(target)!=digest:raise ValueError('Method changed during snapshot creation')
    def verify_method():
        if any(sha(ROOT/name)!=digest for name,digest in result['method_sha256'].items()):
            raise ValueError('Method changed during the run; candidates cannot be pooled')
    def save():(out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    save();shared=out/'shared';best=None;last_candidate_seconds=None
    try:
        with (out/'shared.log').open('w') as log:
            subprocess.run([sys.executable,str(ROOT/'scripts/nextgowin.py'),'--config',str(cfg),'--out',str(shared),
                '--stop-after','resources','--total-timeout',str(max(1,int(budget.remaining()-3)))],stdout=log,stderr=subprocess.STDOUT,check=True)
        base=json.loads((shared/'manifest.json').read_text());frontend=json.loads((shared/'01_frontend/manifest.json').read_text())
        result['shared_resource_sha256']=sha(shared/'03_resources/resources.json');save()
        for recipe in result['recipes']:
            verify_method()
            if last_candidate_seconds is not None and budget.remaining()<last_candidate_seconds*1.5+30:
                result['unstarted_reason']='Remaining shared budget is below the observed candidate cost with a 50% reserve';break
            start=time.monotonic();folder=out/recipe;folder.mkdir();row=dict(recipe=recipe,status='running');result['candidates'].append(row);save()
            try:
                resource=folder/'03_resources';resource.mkdir()
                for name in ('register_choices.json','dsp_cells.v','dsp_output_packing.json'):
                    if (shared/'03_resources'/name).exists():shutil.copyfile(shared/'03_resources'/name,resource/name)
                if (shared/'03_resources/pack_proof').exists():shutil.copytree(shared/'03_resources/pack_proof',resource/'pack_proof')
                if (shared/'03_resources/pack_inverted_enable_proof').exists():shutil.copytree(shared/'03_resources/pack_inverted_enable_proof',resource/'pack_inverted_enable_proof')
                mapped=map_design(shared/'03_resources/resources.json',folder/'04_mapping',config['period_ps'],'arch_delay',recipe)
                row['mapping_manifest_sha256']=sha(folder/'04_mapping/manifest.json')
                materialize(folder/'04_mapping/mapped.json',folder/'05_netlist',frontend['top'],shared/'02_bridge/vendor_cells.v',
                    config.get('max_logic_cells'),config.get('net_aliases',{}),config.get('resource_optimizations',True),
                    config.get('register_nextstate_mapping',False),config.get('register_alternatives',False),config.get('logic_replication',False),
                    config.get('register_replication',False),config.get('dedicated_cluster_replication',False),config.get('register_control_penalty_ps',0))
                vg=folder/'05_netlist/mapped.vg'
                if (shared/'02_bridge/macro.vg').exists():
                    with vg.open('a') as stream:stream.write('\n'+(shared/'02_bridge/macro.vg').read_text())
                limit=int(budget.timeout(config.get('timeout',3600),reserve=10))
                if limit<1:raise TimeoutError('Insufficient total budget to start native routing')
                cmd=[sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(vg),'--sdc',str(path('sdc')),
                     '--out',str(folder/'06_pnr'),'--bitstream','--timeout',str(limit)]
                for key,flag in [('cst','--cst'),('rao','--rao'),('device_config','--device-config')]:
                    if config.get(key):cmd.extend([flag,str(path(key))])
                if config.get('no_iob_registers'):cmd.append('--no-iob-registers')
                elif config.get('output_iob_registers'):cmd.append('--output-iob-registers')
                with (folder/'physical.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
                checked=audit(folder/'06_pnr',profile);(folder/'physical_audit.json').write_text(json.dumps(checked,indent=2)+'\n')
                verify_method()
                score=quality(checked);row.update(status='complete',quality=score,timing_closed=checked['timing_closed'],
                    violated_endpoints=checked['violated_endpoints'],frequency_reports=checked['frequency_reports'],
                    netlist_sha256=sha(vg),bitstream_files=[p.name for p in (folder/'06_pnr').glob('*.fs')])
                if best is None or score>best[0]:best=(score,recipe)
            except Exception as e:row.update(status='failed',error=repr(e))
            last_candidate_seconds=time.monotonic()-start;row['wall_seconds']=last_candidate_seconds;save()
            budget.remaining()
        result.update(status='complete' if best else 'failed',selected_recipe=best[1] if best else None,
                      total_wall_seconds=budget.used(),within_hour=budget.used()<=args.total_timeout)
        if best:
            result['timing_closed']=next(r['timing_closed'] for r in result['candidates'] if r['recipe']==best[1])
            result['selected_artifacts']=dict(netlist=str(out/best[1]/'05_netlist/mapped.vg'),implementation=str(out/best[1]/'06_pnr'),
                physical_audit=str(out/best[1]/'physical_audit.json'))
    except Exception as e:result.update(status='failed',error=repr(e),total_wall_seconds=budget.used(),within_hour=budget.used()<=args.total_timeout)
    save();print(json.dumps(result,indent=2))
    if result['status']!='complete':raise SystemExit(1)


if __name__=='__main__':main()
