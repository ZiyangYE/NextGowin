"""Audit current evidence or reproduce the known WIP induction failure."""
import argparse,hashlib,json,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import sha,load_json,copy_original
from workspace_budget import reserve

TESTS=['test_formal_state_values.py','test_formal_padding_projection.py','test_formal_memory_lanes.py','test_formal_single_clock.py','test_formal_defined_state.py','test_product_observer_audit.py','test_independent_product_proof.py','test_ownpr_image_storage.py','test_equiv_init_transport.py','test_official_source_equivalence.py']
BASE=Path('results/padding_projection_proof_trial_032')


def check():
    data=load_json(ROOT/'checkpoint/artifacts.json')
    for row in data['files']:
        p=(ROOT/row['path']).resolve()
        if not p.is_relative_to(ROOT) or not p.is_file() or p.stat().st_size!=row['bytes']:raise ValueError('Missing or resized artifact '+row['path'])
        with p.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual!=row['sha256']:raise ValueError('Changed artifact '+row['path'])
    return len(data['files'])


def audit():
    count=check();packet=load_json(ROOT/'results/encoder_physical_feedback_trial_028/all_designs_reported_fmax_checkpoint.json');rows=[];verified=set()
    from run_ownpr_pin_closure import metric
    for row in packet['designs']:
        for key in ('selected_source_sha256','functional_certificate_chain'):
            for name,expected in row.get(key,{}).items():
                if sha(ROOT/name)!=expected:raise ValueError('Changed selected evidence '+name)
                verified.add(name)
        measured=metric(ROOT/row['selected']['sta'])
        for key in ('reported_fmax_mhz','setup_wns_ns','hold_wns_ns'):
            if abs(measured[key]-row['metrics'][key])>0.000001:raise ValueError('Native metric mismatch '+row['design'])
        rows.append(dict(design=row['design'],native_reported_mhz=measured['reported_fmax_mhz'],setup_wns_ns=measured['setup_wns_ns'],hold_wns_ns=measured['hold_wns_ns'],competitive_ratio=None))
    return dict(status='retained_evidence_verified',artifact_files=count,selected_artifacts=len(verified),designs=rows,full_source_proved=False,fair_baselines_frozen=False,goal_complete=False)


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['check','audit','tests','projection','reproduce']);a=p.parse_args()
    if a.mode=='check':print(json.dumps(dict(artifact_files=check(),status='stored_hashes_match')));return
    if a.mode=='audit':print(json.dumps(audit(),indent=2));return
    reserve(ROOT,600_000_000);out=ROOT/'checkpoint_runs'/('run_'+str(time.time_ns()));out.mkdir(parents=True);(ROOT/'results').mkdir(exist_ok=True)
    if a.mode=='tests':
        with (out/'pytest.log').open('w') as log:code=subprocess.run([sys.executable,'-m','pytest','-q',*[str(ROOT/'tests'/n) for n in TESTS]],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=180).returncode
        print((out/'pytest.log').read_text());raise SystemExit(code)
    check()
    if a.mode=='projection':
        start=time.time();(out/'budget.json').write_text(json.dumps(dict(start_epoch=start,deadline_epoch=start+900)))
        code=subprocess.run([sys.executable,str(ROOT/'experiments/build_lane_controller.py'),'--pair',str(ROOT/'results/compositional_memory_proof_trial_029/array_pair_001'),'--budget',str(out/'budget.json'),'--out',str(out/'controller'),'--project-padding'],cwd=ROOT,timeout=300).returncode
        if code:raise RuntimeError('Projection rebuild failed')
        fresh=load_json(out/'controller/manifest.json');recorded=load_json(ROOT/BASE/'projected_controller_001/manifest.json')
        for role in ('gold','gate'):
            if fresh[role]['sha256']!=recorded[role]['sha256']:raise ValueError('Rebuilt controller differs from checkpoint')
        print(json.dumps(dict(status='projected_controller_reproduced',full_source_proved=False,output=str(out))));return
    from yosys_runtime import run,quote,YOSYS
    from summarize_product_counterexample import summarize
    expected_binary=load_json(ROOT/BASE/'direct_induction_007/induction.invocation.json')['binary_sha256']
    if sha(YOSYS)!=expected_binary:raise ValueError('Yosys version differs from checkpoint')
    source=ROOT/BASE/'state_values_product_005/with_values.json';copy_original(source,out/'model.json')
    if sha(source)!=sha(out/'model.json'):raise ValueError('Materialized proof model changed')
    try:
        run(['read_json '+quote(out/'model.json'),'hierarchy -top product','check -assert','sat -seq 1 -tempinduct-inductonly -initsteps 3 -maxsteps 4 -set-init-undef -set-def-inputs -prove-asserts -show-ports -dump_vcd '+quote(out/'counterexample.vcd')+' -verify'],out,'induction',300)
    except RuntimeError:
        invocation=load_json(out/'induction.invocation.json');log=(out/'induction.log').read_text()
        if invocation['returncode']!=1 or '[induction step 4] Solving problem' not in log or 'Induction step failed.' not in log:raise
    else:raise ValueError('Unexpected proof success; review changed semantics before accepting')
    result=summarize(out/'counterexample.vcd',load_json(ROOT/BASE/'predecessor_edge_product_004/manifest.json')['helpers'])
    failures=[r for f in result['frames'] for r in f['failures']];allowed={f'ng_mem_{i}_part_0_write_high' for i in (3,4,5,6)}
    if not failures or any(r['port'] not in allowed for r in failures):raise ValueError('Counterexample differs from recorded WIP issue')
    result.update(status='expected_wip_induction_failure_reproduced',controller_proved=False,full_source_proved=False)
    (out/'reproduction.json').write_text(json.dumps(result,indent=2));print(json.dumps(dict(status=result['status'],failed_ports=sorted({r['port'] for r in failures}),output=str(out),full_source_proved=False)))


if __name__=='__main__':main()
