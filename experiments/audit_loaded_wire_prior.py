"""Evaluate a native-load prior on held-out designs and remapped implementations."""
import argparse,collections,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from ownpr_loaded_wire_prior import driver_class,fit,predict
from workspace_budget import reserve

def samples(placement,routes,wire):
    cells=load_json(placement/'packed.json')['modules']['top']['cells'];nets=load_json(routes/'nets.json');measured=load_json(wire/'wire_delays.json')
    if measured['source_routes_sha256']!=sha(routes/'routes.json') or measured['source_nets_sha256']!=sha(routes/'nets.json'):raise ValueError('Wire provenance mismatch')
    meta=load_json(routes/'manifest.json')
    if meta['netlist_sha256']!=sha(placement/'packed.json') or meta['placement_sha256']!=sha(placement/'placement.json') or meta['status']!='routed' or meta['vendor_pnr_executed']:raise ValueError('Unverified own routing provenance')
    drivers={};loads=collections.Counter()
    for name,cell in cells.items():
        for pin,bits in cell['connections'].items():
            for bit in bits:
                if cell['port_directions'][pin]=='input':loads[bit]+=1
                else:
                    if bit in drivers:raise ValueError('Multiple drivers')
                    drivers[bit]=(name,cell['type'])
    rows=[]
    for net in nets:
        # Binder's explicit constant networks have no timed switching driver.
        if net.get('clock') or net['name'] in ('constant_0','constant_1'):continue
        bit=int(net['name'].removeprefix('net_'));name,primitive=drivers[bit];r=measured['nets'][net['name']]
        if {s['resource'] for s in r['sinks']}!=set(net['sinks']):raise ValueError('Native receiver coverage changed')
        values=[s['setup_wire_ns'] for s in r['sinks']]
        if not values:continue
        rows.append(dict(net=net['name'],driver_class=driver_class(primitive),primitive=primitive,logical_fanout=loads[bit],physical_fanout=len(values),mean_setup_wire_ns=sum(values)/len(values),maximum_setup_wire_ns=max(values)))
    return rows

def errors(model,rows):
    a=[abs(r['mean_setup_wire_ns']-.672) for r in rows];b=[abs(r['mean_setup_wire_ns']-predict(model,r['primitive'],r['logical_fanout'])) for r in rows]
    return dict(nets=len(rows),constant_prior_mae_ns=sum(a)/len(a),loaded_prior_mae_ns=sum(b)/len(b),loaded_prior_better=sum(b)<sum(a),scope='Unweighted per-net mean sink-delay error, not endpoint WNS prediction or signoff')

def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--balanced',action='store_true');a=p.parse_args()
    reserve(ROOT,20000000);a.out.mkdir(exist_ok=False);started=time.time();data={};provenance={}
    packet=load_json(a.checkpoint)
    for r in packet['designs']:
        for f,h in r['selected_source_sha256'].items():
            if sha(ROOT/f)!=h:raise ValueError('Selected checkpoint changed: '+f)
        s={k:ROOT/v for k,v in r['selected'].items()};data[r['design']]=samples(s['placement'],s['routes'],s['wire']);provenance[r['design']]={k:str(s[k]) for k in ('placement','routes','wire')}
    def training(excluded=None):return [dict(r,weight=1/len(rows) if a.balanced else 1.) for name,rows in data.items() if name!=excluded for r in rows]
    checks={name:errors(fit(training(name)),rows) for name,rows in data.items()}
    model=fit(training());heldout={}
    # Explicit test fixtures only; no benchmark-specific inference policy.
    for name,placement in [('guided_checkpoint_001','pareto_place_008'),('guided_converged_002','tmds_converged_place_005')]:
        folder=ROOT/'results/resumed_route_search_trial_010'/name
        rows=samples(ROOT/'results/hard_clock_and_budget_convergence_trial_009'/placement,folder/'route',folder/'export/wire')
        heldout[name]=errors(model,rows);data[name]=rows
    result=dict(status='exploratory_prediction_audit',equal_design_training_weight=a.balanced,leave_one_design_out=checks,heldout_remaps=heldout,training_provenance=provenance,model=model,
        no_signoff_or_hold_claim=True,production_default_changed=False,wall_seconds=time.time()-started)
    (a.out/'samples.json').write_text(json.dumps(data));(a.out/'audit.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))

if __name__=='__main__':main()
