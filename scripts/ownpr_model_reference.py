"""Admit native-validated timing graphs without requiring state-alias edits."""
import math
from collections import defaultdict
from pathlib import Path
from ownpr_image_storage import load_json,sha
from ownpr_budgeted_restore import checked_wire


def validate_reconstruction(model,expected,actual,tolerance=1e-5):
    """Check all baseline arcs and finite per-transition AT/RT/slack values."""
    from ownpr_bidir_timing import analyze
    result={}
    for mode in ('setup','hold'):
        def group(graph):
            rows=defaultdict(list)
            for e in graph['edges']:rows[e['source'],e['destination'],e['kind'],tuple(sorted(map(tuple,e['transitions'])))].append(e['delay'])
            return {k:sorted(v) for k,v in rows.items()}
        before,after=group(expected[mode]),group(actual[mode])
        if set(before)!=set(after) or any(len(v)!=len(after[k]) for k,v in before.items()):raise ValueError('Baseline timing arc topology differs')
        edge_error=max((abs(x-y) for k,v in before.items() for a,b in zip(v,after[k]) for x,y in zip(a,b)),default=0.)
        terminals=model['hold_requirements' if mode=='hold' else 'setup_requirements'];old=analyze(expected[mode],terminals,mode=='hold');new=analyze(actual[mode],terminals,mode=='hold');errors={};counts={}
        for field in ('arrival','required','slack'):
            values=[]
            for n,row in old[field].items():
                if n not in new[field]:raise ValueError('Baseline timing node omitted')
                for a,b in zip(row,new[field][n]):
                    if math.isfinite(a) and math.isfinite(b):values.append(abs(a-b))
                    elif a!=b:raise ValueError('Baseline finite/uncovered timing status differs')
            errors[field]=max(values,default=0.);counts[field]=len(values)
        if edge_error>tolerance or max(errors.values())>tolerance:raise ValueError('Baseline full-graph timing reconstruction mismatch')
        result[mode]=dict(maximum_arc_delay_error_ns=edge_error,maximum_node_errors_ns=errors,transition_comparisons=counts,matched_arc_groups=len(before))
    return result


def load_reference(model_dir,placement,routes,wire):
    meta=load_json(model_dir/'manifest.json')
    if meta['status']!='validated_setup_hold_feedback' or sha(model_dir/'model.json')!=meta['model_sha256']:raise ValueError('Unvalidated setup/hold reference model')
    for path,h in meta['source_sha256'].items():
        if sha(Path(path))!=h:raise ValueError('Timing reference input changed')
    packed=[Path(p) for p in meta['source_sha256'] if Path(p).name=='packed.json'];audit=[Path(p) for p in meta['source_sha256'] if Path(p).name=='handoff_audit.json']
    if len(packed)!=1 or len(audit)!=1:raise ValueError('Ambiguous native model reference')
    source=audit[0].parent;native=load_json(source/'manifest.json')
    if sha(placement/'packed.json')==sha(packed[0]) and sha(placement/'placement.json')==native['placement_sha256']:
        rm=load_json(routes/'manifest.json')
        if native['status']!='imported' or not native['sta_completed'] or native['vendor_placement_executed'] or native['vendor_routing_executed']:raise ValueError('Reference is not audited own P&R')
        if native['routes_sha256']!=sha(routes/'routes.json') or sha(source/'clock_tree.json')!=sha(routes/'clock_tree.json') or rm['netlist_sha256']!=sha(placement/'packed.json') or rm['placement_sha256']!=sha(placement/'placement.json'):raise ValueError('Native model/routes binding differs')
        checked_wire(wire,routes);model=load_json(model_dir/'model.json')
        from ownpr_bidir_timing import analyze
        graphs=dict(setup=model['late'],hold=model['early']);metrics={n:analyze(graphs[n],model['hold_requirements' if n=='hold' else 'setup_requirements'],n=='hold') for n in graphs}
        return {n:{k:r[k] for k in ('wns','tns','violations')} for n,r in metrics.items()},graphs,model
    # Compatibility with earlier proven existing-alias checkpoints; the
    # existing evaluator admits only its explicitly supported rewrite scope.
    from evaluate_fixed_clock_rewire import evaluate
    return evaluate(model_dir,placement,routes,wire,True)
