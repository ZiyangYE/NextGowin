"""Independently rebuild candidate setup/hold graphs from complete wire files."""
import argparse,json,time
from pathlib import Path
from ownpr_device import ROOT,Device,RoutingGraph
from ownpr_image_storage import load_json,sha
from ownpr_cell_timing import NativeCellTiming
from ownpr_lut_graph_feedback import FixedBoundaryLutFeedback
from ownpr_model_reference import load_reference
from ownpr_route_design import bind
from ownpr_budgeted_restore import checked_wire
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('reference','model','placement','routes','wire','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,30_000_000);start=time.time()
    row=load_json(a.reference)['designs'][0]
    for name,h in row['selected_source_sha256'].items():
        if sha(ROOT/name)!=h:raise ValueError('Reference checkpoint changed')
    state={n:ROOT/v for n,v in row['selected'].items()}
    _,graphs,model=load_reference(a.model,state['placement'],state['routes'],state['wire'])
    reference=load_json(state['placement']/'packed.json');oldlayout=load_json(state['placement']/'placement.json');design=load_json(a.placement/'packed.json');layout=load_json(a.placement/'placement.json')
    rm=load_json(a.routes/'manifest.json')
    if rm['netlist_sha256']!=sha(a.placement/'packed.json') or rm['placement_sha256']!=sha(a.placement/'placement.json'):raise ValueError('Candidate routes differ')
    if sha(a.routes/'clock_tree.json')!=sha(state['routes']/'clock_tree.json'):raise ValueError('Candidate clock tree differs')
    hard=load_json(state['routes']/'hard_endpoint_audit.json') if oldlayout.get('hard_clusters') else None
    if hard and sha(a.routes/'hard_endpoint_audit.json')!=sha(state['routes']/'hard_endpoint_audit.json'):raise ValueError('Hard endpoints differ')
    graph=RoutingGraph(Device(),ROOT/'results/native_rrg_001');_,oldstatic=bind(reference,oldlayout,graph,hard_bindings=hard)
    logical=[];_,static=bind(design,layout,graph,hard_bindings=hard,logical_sinks=logical)
    wire=checked_wire(a.wire,a.routes)
    measurements={name:{graph.node(s['node'][0]-1,s['node'][1]-1,graph.device.names[s['node'][2]]):s['delays_by_selector'][1][2:4]+s['delays_by_selector'][2][:2] for s in record['sinks']} for name,record in wire['nets'].items()}
    feedback_type=FixedBoundaryLutFeedback
    if rm.get('operator') in ('carry','cone'):
        from ownpr_carry_feedback import FixedBoundaryCarryFeedback
        feedback_type=FixedBoundaryCarryFeedback
    if rm.get('existing_wide_remap'):
        from ownpr_existing_wide_remap import ExistingWideRemapFeedback
        feedback_type=ExistingWideRemapFeedback
    if rm.get('general_wide_recompile'):
        from ownpr_general_wide_recompile import GeneralWideRecompileFeedback
        feedback_type=GeneralWideRecompileFeedback
    if rm.get('cross_macro_recompile'):
        from ownpr_cross_macro_recompile import CrossMacroRecompileFeedback
        feedback_type=CrossMacroRecompileFeedback
    if rm.get('wide_trust_region'):
        from ownpr_wide_trust_region import WideTrustRegionFeedback
        feedback_type=WideTrustRegionFeedback
    feedback=feedback_type(model,graphs,reference,oldlayout,oldstatic,NativeCellTiming(),graph.device)
    feedback.ordinary_group_moves=bool(rm.get('group_ordinary_luts',False));feedback.trust_radius=rm.get('trust_radius',6)
    if feedback.trust_radius not in (6,12):raise ValueError('Unadmitted physical search radius')
    rebuilt=feedback.rebuild(design,layout,logical,measurements,static);metrics=feedback.metrics(rebuilt)
    output=dict(model,late=rebuilt['setup'],early=rebuilt['hold']);payload=json.dumps(output,separators=(',',':'));reserve(ROOT,len(payload.encode())+3_000_000)
    a.out.mkdir(exist_ok=False);(a.out/'model.json').write_text(payload)
    mm=load_json(a.model/'manifest.json');native=load_json(next(Path(f).parent for f in mm['source_sha256'] if Path(f).name=='handoff_audit.json')/'manifest.json')
    sources=[a.reference,a.model/'model.json',a.placement/'packed.json',a.placement/'placement.json',a.routes/'routes.json',a.routes/'clock_tree.json',a.wire/'wire_delays.json',ROOT/'scripts/ownpr_lut_graph_feedback.py',Path(__file__)]
    if rm.get('operator') in ('carry','cone'):sources.extend(ROOT/'scripts'/n for n in ('ownpr_carry_feedback.py','ownpr_extended_timing_model.py','ownpr_carry_macros.py'))
    if rm.get('existing_wide_remap'):sources.extend(ROOT/'scripts'/n for n in ('ownpr_existing_wide_remap.py','ownpr_wide_mapping_feedback.py','reorder_ownpr_wide_inputs.py','ownpr_multilevel_cut.py','ownpr_mux_macros.py'))
    if rm.get('general_wide_recompile') or rm.get('cross_macro_recompile') or rm.get('wide_trust_region'):sources.extend(ROOT/'scripts'/n for n in ('ownpr_general_wide_recompile.py','ownpr_existing_wide_remap.py','ownpr_wide_mapping_feedback.py','ownpr_multilevel_cut.py','ownpr_mux_macros.py','register_control_map.py'))
    if rm.get('cross_macro_recompile') or rm.get('wide_trust_region'):sources.append(ROOT/'scripts/ownpr_cross_macro_recompile.py')
    if rm.get('wide_trust_region'):sources.append(ROOT/'scripts/ownpr_wide_trust_region.py')
    result=dict(status='rebuilt_fixed_boundary_lut_graph',metrics={n:{k:metrics[n][k] for k in ('wns','tns','violations')} for n in metrics},model_sha256=sha(a.out/'model.json'),
                source_sha256={str(f.resolve()):sha(f) for f in sources},
                fixed_constraint_sha256=dict(cst=native['cst_sha256'],sdc=native['source_sdc_sha256']),wall_seconds=time.time()-start,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(dict(status=result['status'],metrics=result['metrics'],wall_seconds=result['wall_seconds']),indent=2))


if __name__=='__main__':main()
