"""Record raw versus retained portfolio outcomes without claiming goal closure."""
import argparse,copy,json
from pathlib import Path
from ownpr_image_storage import load_json,sha
from ownpr_device import ROOT
from workspace_budget import reserve


def record(reference,trial,config,write=True):
    reserve(ROOT,2_000_000);packet=load_json(reference);oldhash=sha(reference);observations=[]
    original=copy.deepcopy(packet)
    for case in packet['designs']:
        for f,h in case['selected_source_sha256'].items():
            if sha(ROOT/f)!=h:raise ValueError('Changed incumbent source '+f)
        plan=config[case['design']];old=copy.deepcopy(case);old_native=ROOT/old['selected']['sta']
        if plan.get('noop'):
            scope=next(r for r in load_json(trial/'cohort_scope_audit.json') if r['design']==case['design'])
            if not scope['no_candidate_noop']:raise ValueError('No-op has eligible candidates')
            case['trial_outcome']=dict(status='no_applicable_structural_cone',raw_candidate_metrics=None,retained_previous=True)
            observations.append(dict(design=case['design'],**case['trial_outcome']));continue
        candidate=trial/plan['candidate'];implementation=trial/plan['implementation'];export=implementation/'export';native=export/'native';route=implementation/'route'
        cm=load_json(candidate/'manifest.json');im=load_json(implementation/'manifest.json');em=load_json(export/'manifest.json');change=load_json(candidate/'change.json')
        if cm['status']!='placed' or im['status']!='checkpoint_handoff_complete' or em['status']!='verified_handoff_complete':raise ValueError('Incomplete physical candidate')
        if cm['netlist_sha256']!=sha(candidate/'packed.json') or cm['placement_sha256']!=sha(candidate/'placement.json'):raise ValueError('Proved candidate changed')
        if cm['source_netlist_sha256']!=sha(ROOT/old['selected']['placement']/'packed.json') or cm['source_layout_sha256']!=sha(ROOT/old['selected']['placement']/'placement.json'):raise ValueError('Wrong candidate lineage')
        if not cm['proof'] or not all(r['cec']['status']=='ok' and r['cec']['equivalent'] for r in cm['proof']):raise ValueError('Boundary CEC did not pass')
        for i,r in enumerate(cm['proof']):
            for filename,key in (('before.blif','reference_sha256'),('after.blif','candidate_sha256')):
                if sha(candidate/'cec'/('module_'+str(i))/filename)!=r['cec'][key]:raise ValueError('CEC artifact changed')
        for f,h in em['source_sha256'].items():
            if sha(Path(f))!=h:raise ValueError('Export source changed')
        for f,h in em['output_sha256'].items():
            if sha(native/f)!=h:raise ValueError('Export evidence changed')
        old_a=load_json(old_native/'handoff_audit.json');a=load_json(native/'handoff_audit.json');metrics=em['metrics']
        nm,om=load_json(native/'manifest.json'),load_json(old_native/'manifest.json')
        if any(nm[k]!=om[k] for k in ('cst_sha256','source_sdc_sha256')):raise ValueError('Native evaluation constraints changed')
        counts={k:r['paths'] for k,r in a['timing']['path_coverage'].items()};old_counts={k:r['paths'] for k,r in old_a['timing']['path_coverage'].items()}
        if counts!=old_counts or a['timing']['pulse_width_checks']!=old_a['timing']['pulse_width_checks']:raise ValueError('Timing inventory changed')
        if a['io']['rows']!=old_a['io']['rows'] or not a['io']['pins_preserved']:raise ValueError('Fixed IO changed')
        if sha(route/'clock_tree.json')!=sha(ROOT/old['selected']['routes']/'clock_tree.json'):raise ValueError('Clock topology changed')
        if not metrics['other_checks_passed'] or metrics['hold_wns_ns']<0:raise ValueError('Non-setup acceptance gate failed')
        gain=metrics['reported_fmax_mhz']-old['metrics']['reported_fmax_mhz']
        use=gain>0 or (gain==0 and change['lut_delta']<0)
        outcome=dict(status='selected_frequency_gain' if gain>0 else 'selected_equal_frequency_area_gain' if use else 'rejected_frequency_regression',
            raw_candidate_metrics=metrics,raw_lut_delta=change['lut_delta'],retained_previous=not use,frequency_delta_mhz=round(gain,6),
            timing_class_counts=counts,pulse_width_checks=a['timing']['pulse_width_checks'],complete_attempt_seconds=im['wall_seconds'],
            candidate_manifest_sha256=sha(candidate/'manifest.json'),native_manifest_sha256=sha(native/'manifest.json'))
        observations.append(dict(design=case['design'],**outcome))
        if use:
            model=trial/plan['model'];mm=load_json(model/'manifest.json')
            if mm['status']!='validated_setup_hold_feedback' or sha(model/'model.json')!=mm['model_sha256']:raise ValueError('Fresh model not validated')
            for f,h in mm['source_sha256'].items():
                if sha(Path(f))!=h:raise ValueError('Model source changed')
            for f in (candidate/'packed.json',candidate/'placement.json',native/'manifest.json'):
                if sha(f) not in mm['source_sha256'].values():raise ValueError('Model not tied to candidate')
            rel=lambda f:Path(f).resolve().relative_to(ROOT).as_posix()
            case['selected'].update(placement=rel(candidate),routes=rel(route),wire=rel(export/'wire'),sta=rel(native))
            case['metrics']=metrics;case['setup_closed_at_evaluation_constraint']=metrics['native_constraints_satisfied']
            case['functional_lineage']=dict(parent_checkpoint_sha256=oldhash,actual_cell_cec_manifest=rel(candidate/'manifest.json'),
                logical_netlist_identical=False,state_and_interface_cycles_unchanged=True)
            files=[candidate/n for n in ('packed.json','placement.json','manifest.json','change.json')]
            files += [route/n for n in ('routes.json','nets.json','all_nets.json','clock_tree.json','manifest.json')]
            files += [native/n for n in ('manifest.json','probe.tr','handoff_audit.json')]
            files += [export/'wire/wire_delays.json',model/'model.json',model/'manifest.json']
            files += [ROOT/old['constraints'][n] for n in ('cst','sdc')]
            case['selected_source_sha256']={rel(f):sha(f) for f in files}
            for f,h in old['selected_source_sha256'].items():
                if f.startswith(old['selected']['clock_capture']+'/'):case['selected_source_sha256'][f]=h
        case.update(trial_outcome=outcome,qualified_mhz=None,competitive_ratio=None,goal_complete=False,
                    native_reported_candidate_only=True,fair_official_baseline_frozen=False,
                    full_bitstream_verified=False,new_algorithm_performance_generalization_proven=False)
    packet['source_report_sha256']=oldhash
    packet['trial_scope']='Raw generic ACD results and explicit measured fallback; fair baselines and full production proof gates remain pending.'
    if not write:return observations
    with (trial/'all_designs_reported_fmax_checkpoint.json').open('x') as f:json.dump(packet,f,indent=2)
    promoted=[r for r in packet['designs'] if r.get('trial_outcome',{}).get('status','').startswith('selected_')]
    with (trial/'reported_fmax_checkpoint.json').open('x') as f:json.dump(dict(designs=promoted,source_report_sha256=oldhash),f,indent=2)
    for r in promoted:
        if not r['design'].isidentifier():raise ValueError('Invalid case filename')
        with (trial/(r['design']+'_reported_fmax_checkpoint.json')).open('x') as f:json.dump(dict(designs=[r],source_report_sha256=oldhash),f,indent=2)
    with (trial/'portfolio_outcomes.json').open('x') as f:json.dump(dict(observations=observations,goal_complete=False,raw_generalization_proven=False),f,indent=2)
    return observations


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--reference',type=Path,required=True);p.add_argument('--trial',type=Path,required=True);p.add_argument('--config',type=Path,required=True);p.add_argument('--audit-only',action='store_true');a=p.parse_args()
    print(json.dumps(record(a.reference,a.trial,load_json(a.config),not a.audit_only),indent=2))
