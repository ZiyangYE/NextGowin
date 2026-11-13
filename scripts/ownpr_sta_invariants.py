"""Fail-closed native STA envelope checks for a single rising-edge clock.

These checks are necessary, not sufficient, for a fixed-configuration replay.
They do not establish equivalence, physical legality, endpoint completeness,
or support for generated/falling-edge/multicycle clock relationships.
"""
import math

MODES=('setup','hold','recovery','removal')


def compare_envelopes(reference,candidate,period_delta_ns=0.,allow_expansion=False,tolerance_ns=.0011):
    if set(reference)!=set(MODES) or set(candidate)!=set(MODES):raise ValueError('All four analysis classes are required')
    if type(period_delta_ns) not in (int,float) or not math.isfinite(period_delta_ns):raise ValueError('Invalid period delta')
    if not 0<tolerance_ns<=.002:raise ValueError('Invalid envelope tolerance')
    result={}
    for mode in MODES:
        old,new=reference[mode],candidate[mode]
        if not old and mode in ('setup','hold'):raise ValueError('Missing '+mode+' reference endpoints')
        missing=set(old)-set(new);extra=set(new)-set(old)
        if missing or extra and not allow_expansion:raise ValueError(f'{mode} endpoint coverage changed: missing={len(missing)}, extra={len(extra)}')
        shifts=(0.,period_delta_ns,period_delta_ns) if mode in ('setup','recovery') else (0.,0.,0.)
        maximum=[0.,0.,0.]
        for key in old:
            for row in (old[key],new[key]):
                if len(row)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) for v in row):raise ValueError('Invalid '+mode+' native envelope')
            for i in range(3):maximum[i]=max(maximum[i],abs(new[key][i]-old[key][i]-shifts[i]))
        result[mode]=dict(reference_endpoints=len(old),candidate_endpoints=len(new),
            maximum_errors_ns=dict(arrival=maximum[0],required_shift=maximum[1],slack_shift=maximum[2]))
        if max(maximum)>tolerance_ns:raise ValueError(mode+' native timing invariants failed: '+str(result[mode]['maximum_errors_ns']))
    return result
