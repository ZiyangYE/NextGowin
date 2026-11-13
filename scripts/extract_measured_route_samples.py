"""Export native-measured ordinary-net samples, grouped by full routed net.

Post-route topology features are not available to an unrouted placer. Samples
are diagnostic/training inputs, not delay bounds or accepted timing predictions.
"""
import argparse,json,math
from pathlib import Path
from ownpr_image_storage import load_json,sha
from ownpr_device import ROOT
from workspace_budget import reserve

def extract(routes,wire):
    rrg=load_json(ROOT/'results/native_rrg_001/manifest.json')
    if rrg['open_chip_database_used'] or rrg['status']!='built':raise ValueError('Expected native routing graph')
    n,cols=rrg['node_types'],rrg['columns'];position=lambda node:(node//n//cols,node//n%cols)
    nets=load_json(routes/'nets.json');trees=load_json(routes/'routes.json');delays=load_json(wire/'wire_delays.json')
    if trees['status']!='routed' or delays['status']!='own_routes_wire_delays_measured':raise ValueError('Incomplete routing/measurement')
    if {v['name'] for v in nets}!=set(trees['nets']):raise ValueError('Net inventory differs from routing')
    samples=[]
    for net in nets:
        if net.get('clock') or 'constant_value' in net:continue
        name=net['name'];measured=delays['nets'][name]['sinks'];wanted=set(net['sinks'])
        if {s['resource'] for s in measured}!=wanted:raise ValueError('Measured endpoint set differs: '+name)
        source=position(net['source']);points=[source]+[position(s) for s in net['sinks']]
        for sink in measured:
            dest=position(sink['resource']);setup=sink['setup_wire_ns'];hold=sink['hold_wire_ns']
            if not all(math.isfinite(v) and v>=0 for v in (setup,hold)):raise ValueError('Invalid measured delay')
            samples.append(dict(net=name,source_resource=net['source'],sink_resource=sink['resource'],dx=abs(dest[1]-source[1]),dy=abs(dest[0]-source[0]),fanout=len(wanted),span_x=max(p[1] for p in points)-min(p[1] for p in points),span_y=max(p[0] for p in points)-min(p[0] for p in points),routed_pips=len(trees['nets'][name]['pips']),setup_wire_ns=setup,hold_wire_ns=hold))
    return dict(status='native_measured_route_dataset',prediction_validated=False,coordinates='canonical RRG coordinates; not geometric lower bounds',split_requirement='Group validation by design and entire net; do not random-split sinks of the same net',post_route_features=['routed_pips'],source_sha256={str(p):sha(p) for p in (routes/'nets.json',routes/'routes.json',wire/'wire_delays.json')},samples=samples)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('routes','wire','out'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,30_000_000);result=extract(a.routes,a.wire)
    with a.out.open('x') as stream:json.dump(result,stream,separators=(',',':'))
    print('measured samples',len(result['samples']))
