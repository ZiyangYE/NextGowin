"""Independently check actual native shared controls and distinct paired data."""
import argparse,json
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from ownpr_shared_ff_packing import compile_sites


def check(design,placed,device,captures,bindings):
    _,info=compile_sites(design,placed,device);pins={};located={}
    for row in captures:
        key=row['instance'],row['pin']
        if key in pins:raise ValueError('Duplicate native captured pin')
        pins[key]=row
    for row in bindings['bindings']:
        if row['instance'] in located:raise ValueError('Duplicate native physical binding')
        located[row['instance']]=row
    records=[]
    for group in info['records']:
        r,c=group['tile'];controls={};qs=[]
        for ff,lut,slot in zip(group['ffs'],group['luts'],group['slots']):
            if located[ff]['kind']!='FF' or located[lut]['kind']!='LUT' or located[ff]['site']!=[r+1,c+1,slot] or located[lut]['site']!=[r+1,c+1,slot]:raise ValueError('Shared pair actual placement differs')
            d=pins[ff,'D']
            if d['nodes'] or not d['direct_connection']:raise ValueError('Shared FF lost paired direct D data')
            physical=device.register_bindings_for('DFFRE')[slot]
            for pin in ('CLK','CE','RESET','Q'):
                nodes=[row['node'] for row in pins[ff,pin]['nodes']];expected=[r+1,c+1,device.ids[physical[pin]]]
                if nodes!=[expected]:raise ValueError('Shared FF actual pin differs: '+ff+'/'+pin)
                if pin=='Q':qs.append(expected)
                elif pin in controls and controls[pin]!=expected:raise ValueError('Native controls are not shared')
                else:controls[pin]=expected
        if qs[0]==qs[1]:raise ValueError('Shared FF outputs are not independent')
        records.append(dict(tile=group['tile'],ffs=group['ffs'],slots=group['slots'],controls=controls,q_nodes=qs,paired_direct_data=True))
    return dict(status='shared_ff_native_bindings_verified',pairs=len(records),registers=2*len(records),records=records,calibration_sha256=info['calibration_sha256'],
        scope='Native physical controls, independent Q pins and paired D links checked for every admitted pair. Full loaded timing/configuration/IO acceptance remains a separate audit.')


def audit(folder,placement):
    folder,placement=Path(folder),Path(placement);m=load_json(folder/'manifest.json')
    if m['packed_sha256']!=sha(placement/'packed.json') or m['placement_sha256']!=sha(placement/'placement.json') or not m['sta_report_generated'] or m['vendor_placement_executed'] or m['vendor_routing_executed']:raise ValueError('Shared route audit source mismatch')
    result=check(load_json(placement/'packed.json'),load_json(placement/'placement.json'),Device(),load_json(folder/'sta_endpoint_audit.json'),load_json(folder/'binding_report.json'))
    result['source_sha256']={str(p):sha(p) for p in (placement/'packed.json',placement/'placement.json',folder/'sta_endpoint_audit.json',folder/'binding_report.json')}
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--native',type=Path,required=True);p.add_argument('--placement',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from workspace_budget import reserve
    reserve(ROOT,1_000_000);result=audit(a.native,a.placement)
    if a.out.exists():raise FileExistsError(a.out)
    a.out.write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ('records','source_sha256')},indent=2))
