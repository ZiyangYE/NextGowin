"""Verify the admitted constant-inactive global reset and its native configuration."""
import argparse,json
from pathlib import Path
from ownpr_device import Device,ROOT
from ownpr_image_storage import load_json,sha
from ownpr_io_reference import load as reference
from ownpr_fuse_audit import SwitchChoices
from ownpr_lut_configuration import NativeFrameProjection
from workspace_budget import reserve

def audit(folder,reference_folder):
    snapshot=load_json(folder/'physical_before.json');state=load_json(folder/'io_configuration_state.json');ref=reference(reference_folder,folder/'project.cst')
    if not all(state['gsr'].values()) or state['gsr']!=ref['state']['gsr'] or snapshot['io']['gsr']!=state['gsr']:raise ValueError('Global reset binding differs from calibrated reference')
    rows=[(key,p) for key,p in snapshot['logical_pin_nodes'].items() if p['logical_class']=='.?AVGsr@NETLIST@@']
    if len(rows)!=1:raise ValueError('Global reset input inventory is not unique')
    key,pin=rows[0];driver,sinks=snapshot['logical_net_connections'][str(pin['net'])];source=snapshot['logical_pin_nodes'].get(str(driver))
    if int(key) not in sinks or not pin['label'].endswith('.GSRI') or pin['inverted'] or not source or source['logical_class']!='.?AVVcc@NETLIST@@':raise ValueError('Dynamic or non-inactive global reset is not admitted')
    device=Device();projection=NativeFrameProjection(device);choices=SwitchChoices(device);image=projection.read(folder/'probe.fs');refimage=projection.read(reference_folder/'probe.fs');bits=[]
    if device.grid[108][180]!=220:raise ValueError('GSR configuration site changed')
    for table,selector in ((20,(1,0)),(60,(8,0))):
        entries=[r for r in device.fse.read_rows(device.fse.get_table('220',table)) if tuple(r[:2])==selector]
        if len(entries)!=1:raise ValueError('Ambiguous GSR feature')
        for fuse in entries[0][2:]:
            if fuse<0:continue
            bit=(108,180,*choices.bit(220,fuse));index=projection.index(bit)
            if int(image[index])!=49 or int(refimage[index])!=49:raise ValueError('Global reset configuration differs')
            bits.append(bit)
    if len(bits)!=2:raise ValueError('GSR mask changed')
    files=[folder/'physical_before.json',folder/'io_configuration_state.json',folder/'probe.fs',folder/'project.cst',reference_folder/'manifest.json',reference_folder/'io_configuration_state.json',reference_folder/'probe.fs']
    return dict(status='constant_inactive_gsr_and_encoded_binding_verified',driver=source['label'],sink=pin['label'],bits=bits,source_sha256={str(p):sha(p) for p in files},scope='Existing constant-inactive GSR profile only; no dynamic reset or new power-up assumption introduced')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--reference',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();reserve(ROOT,1000000);r=audit(a.folder,a.reference)
    with a.out.open('x') as f:json.dump(r,f,indent=2)
    print(r['status'])
