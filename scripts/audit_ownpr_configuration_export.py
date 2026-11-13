"""Independent configuration handoff checks, deliberately separate from STA."""
import argparse,json
from pathlib import Path
from audit_ownpr_native_export import connectivity_check,io_check,sha
from audit_native_io_configuration import check_state
from ownpr_device import Device,DLL_SHA
from ownpr_lut_configuration import NativeFrameProjection
from ownpr_frame_integrity import audit as audit_frames
from ownpr_place import constraints
from ownpr_image_storage import load_json


def audit(folder,placement):
    folder=Path(folder);placement=Path(placement)
    read=load_json
    meta=read(folder/'manifest.json');bound=read(folder/'binding_report.json')
    if meta['status'] not in ('imported','handoff_audit_failed') or meta['dll_sha256']!=DLL_SHA or meta['vendor_placement_executed'] or meta['vendor_routing_executed']:
        raise ValueError('Not an own-P&R configuration handoff')
    if (not bound['encoding_preserved_parents'] or not bound['encoding_preserved_geometry'] or bound['encoder_profile']!='fresh_process_single_pass'):
        raise ValueError('Encoding did not preserve own P&R')
    for path,key in ((placement/'packed.json','packed_sha256'),(placement/'placement.json','placement_sha256'),(folder/'probe.vg','exported_netlist_sha256'),
        (folder/'project.cst','cst_sha256'),(folder/'probe.sdc','sdc_sha256'),(folder/'clock_tree.json','clock_tree_sha256'),
        (folder/'expanded_routes.json','expanded_routes_sha256'),(folder/'partial_configuration.json','partial_configuration_sha256'),(folder/'io_reference.json','io_reference_sha256')):
        if sha(path)!=meta[key]:raise ValueError('Configuration handoff input changed: '+str(path))
    design=read(placement/'packed.json');graph=connectivity_check(design,read(folder/'name_map.json'),read(folder/'native_connectivity.json'),bound)
    partial=read(folder/'partial_configuration.json')
    if partial['conflicts']:raise ValueError('Conflicting own configuration')
    image=NativeFrameProjection(Device());values=image.read(folder/'probe.fs');mismatches=[]
    for row in partial['bits']:
        bit=tuple(row['tile']+row['local_bit']);actual=int(values[image.index(bit)])-48
        if actual!=row['value']:mismatches.append(dict(bit=bit,expected=row['value'],actual=actual))
    contract=constraints(design['modules']['top'],(folder/'project.cst').read_text(),(folder/'probe.sdc').read_text())
    io=io_check((folder/'probe.pin.html').read_text(),contract)
    reference=read(folder/'io_reference.json');state=check_state(read(folder/'io_state_after_encoding.json'),reference['state'])
    frames=audit_frames(folder/'probe.fs')
    result=dict(status='known_configuration_matched' if not mismatches and frames['status']=='frame_integrity_verified' else 'failed',
        known_configuration_bits=len(partial['bits']),mismatches=mismatches,graph=graph,io=io,io_state=state,frame_integrity=frames,
        source_sha256={p.name:sha(p) for p in (folder/'probe.fs',folder/'manifest.json',folder/'binding_report.json',folder/'native_connectivity.json',folder/'io_state_after_encoding.json')},
        native_encoder=True,vendor_placement_executed=False,vendor_routing_executed=False,timing_signoff=False,full_bitstream_verified=False,
        scope='Known LUT/FF/ALU/data/clock bits, I/O state, graph and frame CRC only; dedicated carry timing, async checks, full modes and hardware remain unverified.')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--placement',type=Path,required=True);a=p.parse_args()
    r=audit(a.folder,a.placement);(a.folder/'configuration_handoff_audit.json').write_text(json.dumps(r,indent=2))
    print(r['status'],'bits',r['known_configuration_bits'],'mismatches',len(r['mismatches']))
    if r['status']!='known_configuration_matched':raise SystemExit(1)
