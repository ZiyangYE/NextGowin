"""Match every replicated macro input to measured whole-tree sink timing."""
import argparse,json
from pathlib import Path
from ownpr_device import ROOT
from ownpr_image_storage import load_json,sha
from summarize_path_regions import endpoint_table


def capture(folder):
    route=folder/'implementation/route';file=folder/'implementation/export/wire/wire_delays.json';wire=load_json(file)
    if wire['source_routes_sha256']!=sha(route/'routes.json') or wire['source_nets_sha256']!=sha(route/'nets.json') or wire['vendor_pnr_executed']:raise ValueError('Wire measurements do not match own routes')
    pins={}
    for row in load_json(route/'search_weights.json')['policy']['receivers']:
        for pin in row['receivers']:
            key=tuple(pin)
            if key in pins:raise ValueError('Ambiguous logical receiver')
            pins[key]=row['net'],row['node']
    sinks={(net,x['resource']):x for net,v in wire['nets'].items() for x in v['sinks']}
    return pins,sinks,wire


def compare(old,new):
    op,ow,om=capture(old);np,nw,nm=capture(new)
    if om['speed_context']!=nm['speed_context'] or om['calibration_reference_sha256']!=nm['calibration_reference_sha256']:raise ValueError('Timing contexts differ')
    endpoints,period=endpoint_table(new/'implementation/export/native');plan=load_json(new/'candidate/input_frontier_plan.json');rows=[]
    oldcells=load_json(old/'candidate/packed.json')['modules']['top']['cells'];newcells=load_json(new/'candidate/packed.json')['modules']['top']['cells']
    proof=load_json(new/'candidate/manifest.json')['proof']
    if proof['status']!='register_replica_invariant_proved':raise ValueError('Missing combined state proof')
    for row in plan['selected']:
        clone=next(iter(row['change']['register_replicas']));parent=row['parent'];wires=[]
        for name,pin in row['sinks']:
            if oldcells[name]['connections'][pin]!=oldcells[parent]['connections']['Q'] or newcells[name]['connections'][pin]!=newcells[clone]['connections']['Q']:raise ValueError('Receiver no longer matches the state-copy certificate')
            before=ow[op[name,pin]];after=nw[np[name,pin]]
            wires.append(dict(endpoint=name+'/'+pin,old_net=op[name,pin][0],new_net=np[name,pin][0],old_setup_wire_ns=before['setup_wire_ns'],new_setup_wire_ns=after['setup_wire_ns'],old_hold_wire_ns=before['hold_wire_ns'],new_hold_wire_ns=after['hold_wire_ns']))
        rows.append(dict(parent=parent,clone=clone,macro=row['root'],target_input_screen_ns=row['target_input_headroom_ns'],actual_clone_setup_slack={p:endpoints.get(clone+'/'+p) for p in ('D','CE','RESET','SET')},wires=wires))
    return dict(records=rows,period_ns=period,source_plan_sha256=sha(new/'candidate/input_frontier_plan.json'),old_wire_sha256=sha(old/'implementation/export/wire/wire_delays.json'),new_wire_sha256=sha(new/'implementation/export/wire/wire_delays.json'),sta_sha256=sha(new/'implementation/export/native/probe.tr'),
        scope='Actual native whole-tree receiver max/min components, not one RR arc or whole-path gain. Clone endpoint slack uses the reported period; null means no reported setup endpoint.')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--old',type=Path,required=True);p.add_argument('--new',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from workspace_budget import reserve
    reserve(ROOT,1000000);result=compare(a.old,a.new);a.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps([dict(parent=r['parent'],setup=r['actual_clone_setup_slack'],wire_changes=[(round(w['old_setup_wire_ns'],3),round(w['new_setup_wire_ns'],3)) for w in r['wires']]) for r in result['records']],indent=2))
