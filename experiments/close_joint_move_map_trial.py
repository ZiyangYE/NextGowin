"""Close trial012 using complete native evidence; preserve pending goal gates."""
import copy,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from run_ownpr_pin_closure import metric

def main():
    r=ROOT/'results/joint_wide_move_map_trial_012';reserve(ROOT,2000000);rel=lambda p:p.resolve().relative_to(ROOT).as_posix()
    previous=ROOT/'results/clock_data_cooptimization_trial_007/all_designs_reported_fmax_checkpoint.json';packet=load_json(previous)
    for row in packet['designs']:
        for f,h in row['selected_source_sha256'].items():
            if sha(ROOT/f)!=h:raise ValueError('Previous evidence changed')
    row=next(x for x in packet['designs'] if x['design']=='tmds');old=copy.deepcopy(row)
    search=r/'search_001';export=r/'export_003';native=export/'native';model=r/'bidir_model_008'
    sm=load_json(search/'manifest.json');em=load_json(export/'manifest.json');nm=load_json(native/'manifest.json');bm=load_json(model/'manifest.json');audit=load_json(native/'handoff_audit.json');gm=load_json(r/'whole_graph_prediction_audit.json')
    if sm['status']!='routed' or not all(x['cec']['equivalent'] for x in sm['proof']['checks']):raise ValueError('Missing final actual-cell proof')
    if em['status']!='verified_handoff_complete' or bm['status']!='validated_setup_hold_feedback' or gm['status']!='complete_graph_prediction_matches_independent_native_model':raise ValueError('Incomplete selected validation')
    m=metric(native)
    if m!=em['metrics'] or not m['other_checks_passed'] or m['hold_violations'] or m['reported_fmax_mhz']<=old['metrics']['reported_fmax_mhz']:raise ValueError('No eligible native gain')
    if any(em[k] for k in ('vendor_placement_executed','vendor_routing_executed','open_chip_database_used')):raise ValueError('Non-own provenance')
    for p,k in [(search/'placement/packed.json','packed_sha256'),(search/'placement/placement.json','placement_sha256'),(ROOT/row['constraints']['cst'],'cst_sha256'),(ROOT/row['constraints']['sdc'],'source_sdc_sha256')]:
        if sha(p)!=nm[k]:raise ValueError('Native implementation binding changed')
    old_audit=load_json(ROOT/old['selected']['sta']/'handoff_audit.json')
    if {k:v['paths'] for k,v in audit['timing']['path_coverage'].items()}!={k:v['paths'] for k,v in old_audit['timing']['path_coverage'].items()}:raise ValueError('Changed timing path class inventory')
    guards=load_json(r/'cohort_guards_002/summary.json')
    if len(guards['designs'])!=4 or any(len(x['cases'])!=3 for x in guards['designs']):raise ValueError('Missing cohort guards')
    for x in guards['designs']:
        for c in x['cases']:
            if not all(p['cec']['equivalent'] for p in c['proof']):raise ValueError('Cohort proof failed')
    row['selected'].update(placement=rel(search/'placement'),routes=rel(search),wire=rel(export/'wire'),sta=rel(native))
    row.update(metrics=m,validated_bidir_model=rel(model),source_checkpoint=rel(previous),functional_lineage=dict(parent_checkpoint_sha256=sha(previous),actual_cell_cec_manifest=rel(search/'manifest.json'),logical_netlist_identical=False,state_and_interface_cycles_unchanged=True))
    files=[search/'placement/packed.json',search/'placement/placement.json',search/'placement/manifest.json',search/'manifest.json',search/'selected.json',*[search/n for n in ('routes.json','nets.json','all_nets.json','clock_tree.json')],export/'wire/wire_delays.json',*[native/n for n in ('manifest.json','handoff_audit.json','probe.tr')],model/'model.json',model/'manifest.json',r/'whole_graph_prediction_audit.json',r/'cohort_guards_002/summary.json',ROOT/row['constraints']['cst'],ROOT/row['constraints']['sdc'],ROOT/row['selected']['clock_capture']/'encoding_audit.json']
    row['selected_source_sha256']={rel(f):sha(f) for f in files}
    packet['source_report_sha256']=sha(previous)
    (r/'reported_fmax_checkpoint.json').write_text(json.dumps(dict(designs=[row],source_report_sha256=sha(previous)),indent=2));(r/'all_designs_reported_fmax_checkpoint.json').write_text(json.dumps(packet,indent=2))
    budget=load_json(r/'budget.json');now=time.time()
    if now>=budget['deadline_epoch']:raise TimeoutError('Original trial deadline exceeded')
    complete=dict(status='native_candidate_recorded_joint_goal_unfinished',wall_seconds=now-budget['start_epoch'],within_original_deadline=True,old_mhz=old['metrics']['reported_fmax_mhz'],new_mhz=m['reported_fmax_mhz'],metrics=m,tests_passed=19,cohort_structural_cases=12,measured_candidates=sm['measured_candidates'],selected_transactions=sm['accepted_transactions'],goal_complete=False,fair_official_baselines_frozen=False,
        preserved_diagnostics=['Export preflight rejected the 1GB reservation before writing; rejected trial010 FS compressed losslessly under cleanup inventory.','time_states_004 used the older selected-pin API and failed bidir schema admission; time_states_007 is the correct full-state capture.','Initial graph comparison required bitwise-equal inactive sentinel slack; one differed by1.91e-6ns. Final audit checks active/inactive classification and numerical tolerance for all finite values.'])
    (r/'completion_audit.json').write_text(json.dumps(complete,indent=2));print(json.dumps(complete,indent=2))

if __name__=='__main__':main()
