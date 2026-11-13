"""Unrealizable ideal useful-skew bound from every launch/endpoint path.

Same insertion shift at slow/fast corners, independent per FF, no clock routing,
dispersion or pulse-width proof. Difference constraints preserve all setup/hold
paths, including competing rise/fall launches and fixed primary IO boundaries.
"""
import math
from ownpr_bidir_timing import topology

ZERO='<fixed-primary-clock>'


def constraints(model,ffs):
    result=[]
    for mode,early,key in (('setup',False,'late'),('hold',True,'early')):
        graph=model[key];terms=model[mode+'_requirements'];order,incoming,_=topology(graph)
        initial=math.inf if early else -math.inf;choose=min if early else max
        for seed,values in graph['seeds'].items():
            launch=seed.rsplit('/',1)[0];launch=launch if launch in ffs else ZERO
            arrival={n:[initial,initial] for n in order};arrival[seed]=list(values)
            for n in order:
                for i in incoming[n]:
                    e=graph['edges'][i]
                    for a,b in e['transitions']:arrival[n][b]=choose(arrival[n][b],arrival[e['source']][a]+e['delay'][b])
            for point,required in terms.items():
                capture=point.rsplit('/',1)[0];capture=capture if capture in ffs else ZERO
                slacks=[(arrival[point][i]-required[i] if early else required[i]-arrival[point][i]) for i in (0,1) if abs(required[i])<1e9 and math.isfinite(arrival[point][i])]
                if slacks:
                    # x[v] <= x[u] + slack (+ delta-period for setup).
                    u,v=(launch,capture) if early else (capture,launch)
                    result.append(dict(u=u,v=v,slack=min(slacks),period_coefficient=0 if early else 1,mode=mode,seed=seed,endpoint=point))
    return result


def feasible(rows,ffs,delta_period,shift_limit=None):
    nodes=[ZERO,*sorted(ffs)];edges=[(r['u'],r['v'],r['slack']+r['period_coefficient']*delta_period) for r in rows]
    if shift_limit is not None:
        if not math.isfinite(shift_limit) or shift_limit<0:raise ValueError('Invalid shift limit')
        edges.extend((u,v,shift_limit) for n in ffs for u,v in ((ZERO,n),(n,ZERO)))
    distances=dict.fromkeys(nodes,0.)
    for _ in nodes:
        changed=False
        for u,v,w in edges:
            if distances[v]>distances[u]+w+1e-10:distances[v]=distances[u]+w;changed=True
        if not changed:return {n:distances[n]-distances[ZERO] for n in ffs}
    return None


def bound(model,ffs,period,shift_limit=None):
    rows=constraints(model,ffs);lo=0.;hi=max(period,1.)
    while feasible(rows,ffs,hi-period,shift_limit) is None:
        hi*=2
        if hi>1e6:raise ValueError('No feasible ideal setup/hold assignment')
    for _ in range(45):
        mid=(lo+hi)/2
        if feasible(rows,ffs,mid-period,shift_limit) is None:lo=mid
        else:hi=mid
    shifts=feasible(rows,ffs,hi+1e-7-period,shift_limit)
    return dict(ideal_minimum_period_ns=hi,ideal_fmax_mhz_not_native=1000/hi,shifts_ns=shifts,
        absolute_shift_limit_ns=shift_limit,constraint_count=len(rows),setup_constraints=sum(r['mode']=='setup' for r in rows),
        hold_constraints=sum(r['mode']=='hold' for r in rows),clock_route_realizability_proven=False,timing_signoff=False)
