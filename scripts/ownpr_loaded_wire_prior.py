"""Exploratory native-wire prior. No placement, hold or signoff guarantee."""
import math
from collections import defaultdict

def driver_class(primitive):
    if primitive in ('IBUF','IOBUF'):return 'input_io'
    if primitive.startswith('LUT'):return 'lut'
    if primitive.startswith('MUX2_LUT'):return 'wide_mux'
    if primitive.startswith('DFF'):return 'register'
    if primitive=='ALU':return 'alu'
    return 'other'

def fit(samples):
    """Group intercepts, nonnegative within-group log-fanout slope; one vote per net."""
    groups=defaultdict(list)
    for row in samples:
        if row['logical_fanout']<1 or not math.isfinite(row['mean_setup_wire_ns']) or row['mean_setup_wire_ns']<0:raise ValueError('Invalid native wire sample')
        weight=row.get('weight',1.)
        if not math.isfinite(weight) or weight<=0:raise ValueError('Invalid sample weight')
        groups[row['driver_class']].append((math.log2(1+row['logical_fanout']),row['mean_setup_wire_ns'],weight))
    if not groups:raise ValueError('Empty native calibration')
    means={k:(sum(w*x for x,y,w in v)/sum(w for x,y,w in v),sum(w*y for x,y,w in v)/sum(w for x,y,w in v)) for k,v in groups.items()}
    numerator=sum(w*(x-means[k][0])*(y-means[k][1]) for k,v in groups.items() for x,y,w in v)
    denominator=sum(w*(x-means[k][0])**2 for k,v in groups.items() for x,y,w in v)
    slope=max(0.,numerator/denominator) if denominator else 0.
    return dict(format='native_net_mean_wire_prior_v1',slope=slope,intercepts={k:y-slope*x for k,(x,y) in means.items()},
        training_nets=sum(map(len,groups.values())),scope=__doc__,native_timing_verified=False)

def predict(model,primitive,fanout,fallback=.672):
    if fanout<1:raise ValueError('Wire prediction requires a load')
    if not math.isfinite(model['slope']) or model['slope']<0:raise ValueError('Invalid prior slope')
    key=driver_class(primitive)
    if key not in model['intercepts']:return fallback
    if not math.isfinite(model['intercepts'][key]):raise ValueError('Invalid prior intercept')
    return max(0.,model['intercepts'][key]+model['slope']*math.log2(1+fanout))
