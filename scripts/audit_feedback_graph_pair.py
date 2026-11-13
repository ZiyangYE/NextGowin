"""Compare all graph edges and RF values against an independently rebuilt model."""
import collections,math
from ownpr_bidir_timing import analyze

def audit(a,b,tolerance=.000002):
    rows={}
    for mode,g,req in [('setup','late','setup_requirements'),('hold','early','hold_requirements')]:
        if set(a[g]['nodes'])!=set(b[g]['nodes']):raise ValueError('Changed graph inventory')
        def groups(graph):
            d=collections.defaultdict(list)
            for e in graph['edges']:d[(e['source'],e['destination'],e['kind'],tuple(sorted(map(tuple,e['transitions']))))].append(tuple(e['delay']))
            return {k:sorted(v) for k,v in d.items()}
        x,y=groups(a[g]),groups(b[g])
        if set(x)!=set(y):raise ValueError('Changed edge inventory or transition relation')
        errors=[]
        for k in x:
            if len(x[k])!=len(y[k]):raise ValueError('Changed edge multiplicity')
            errors.extend(abs(u-v) for p,q in zip(x[k],y[k]) for u,v in zip(p,q))
        if not errors or any(not math.isfinite(e) or e>=tolerance for e in errors):raise ValueError('Changed edge delay')
        aa=analyze(a[g],a[req],mode=='hold');bb=analyze(b[g],b[req],mode=='hold');counts={};maximum={};sentinels=0
        for field in ('arrival','required','slack'):
            if set(aa[field])!=set(bb[field]):raise ValueError('Changed timing inventory')
            es=[]
            for n in aa[field]:
                for u,v in zip(aa[field][n],bb[field][n]):
                    if math.isfinite(u) and math.isfinite(v):
                        if (abs(u)>=1e9)!=(abs(v)>=1e9):raise ValueError('Changed active transition')
                        if abs(u-v)>=tolerance:raise ValueError('Changed timing value')
                        if abs(u)>=1e9:sentinels+=1
                        else:es.append(abs(u-v))
                    elif u!=v:raise ValueError('Changed reachability')
            if not es:raise ValueError('Vacuous timing comparison')
            counts[field]=len(es);maximum[field]=max(es)
        rows[mode]=dict(edge_groups=len(x),maximum_edge_error_ns=max(errors),comparisons=counts,maximum_errors_ns=maximum,inactive_finite_sentinel_comparisons=sentinels)
    return dict(status='complete_graph_prediction_matches_independent_native_model',tolerance_ns=tolerance,results=rows)
