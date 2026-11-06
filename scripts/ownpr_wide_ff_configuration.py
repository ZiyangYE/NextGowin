"""Calibrated same-CFU wide-root/FF input selection, not a complete packer.

The ordinary graph intentionally excludes DI nodes. These connections must
only be added at explicitly selected, separately legalized wide/FF sites.
Full-design STA, clocks and placement legality remain required.
"""
import argparse,json,math
from pathlib import Path
import numpy as np
from ownpr_device import Device,DLL_SHA,RoutingGraph
from ownpr_ff_configuration import single_ff_attributes
from ownpr_fuse_audit import SwitchChoices
from ownpr_lut_configuration import NativeFrameProjection,LutInitChoices
from ownpr_cls_configuration import ClsChoices
from ownpr_image_storage import load_json,sha
from audit_native_encoding_probe import payload

ROOT_SLOT={5:0,6:1,7:3}


def groups(level,slot,profile):
    if level not in ROOT_SLOT or slot!=ROOT_SLOT[level]:raise ValueError('Uncalibrated wide FF root slot')
    if (profile.get('cell_type')!='DFFRE' or (profile.get('ce'),profile.get('control')) not in (('one','zero'),('one','signal'),('signal','zero'))
            or profile.get('clock')!='signal' or set(profile)!={'cell_type','ce','control','clock'}):
        raise ValueError('Uncalibrated wide FF control profile')
    result=single_ff_attributes(slot=slot,**profile)
    for g in range(1<<(level-5)):result[g][0]=9
    return result


def audit_capture(folder):
    folder=Path(folder);meta=load_json(folder/'manifest.json');device=Device();projection=NativeFrameProjection(device)
    if (meta.get('status')!='captured' or meta.get('dll_sha256')!=DLL_SHA or meta.get('vendor_pnr_executed') is not False
            or meta.get('device_programmed') is not False or sha(folder/'encoding.json')!=meta.get('encoding_sha256')
            or sha(folder/'routes.json')!=meta.get('routes_sha256') or sha(folder/'probe.vg')!=meta.get('source_sha256')):
        raise ValueError('Unverified wide FF capture provenance')
    routes=load_json(folder/'routes.json');encoded=load_json(folder/'encoding.json')
    if not routes['saved_physical_fields_restored'] or routes['production_enabled'] or encoded['production_enabled']:
        raise ValueError('Unexpected wide FF capture scope')
    route_rows={(r['tile_type'],int(r['driver']),r['ff_slot']):r for r in routes['records']}
    if len(route_rows)!=len(routes['records']):raise ValueError('Duplicate wide FF route query')
    switches=SwitchChoices(device);luts=LutInitChoices(device);cls=ClsChoices(device);seen=set();checked=[]
    for row in encoded['records']:
        typ,level,slot=row['tile_type'],row['level'],row['slot'];key=typ,level,slot
        if key in seen or typ not in (17,228):raise ValueError('Duplicate or uncalibrated wide FF site')
        seen.add(key);r,c=row['site'];r-=1;c-=1
        if device.grid[r][c]!=typ or row['source_fse_sha256']!=device.fse.sha256:raise ValueError('Wide FF FSE/site mismatch')
        profile=row['ff_profile'];expected_groups=groups(level,slot,profile);q=route_rows.get(key)
        src,dst=[r+1,c+1,48+slot],[r+1,c+1,295+slot]
        if (not q or q['ff_profile']!=profile or q['site']!=row['site'] or q['source_data_points']!=[src] or q['data_points']!=[dst]
                or q['selector_input_conflict'] or q['leaf_input_conflict'] or q['direct']
                or q['source_direct_fanouts_to_data']!=[dict(source=src,destination=dst,edge_present=True)]
                or row['source']!=src or row['destination']!=dst):raise ValueError('Wide FF logical/physical connection mismatch')
        images={}
        for label in ('warmup','baseline','linked','restored'):
            image=row[label];path=folder/image['file']
            if path.parent.resolve()!=folder.resolve() or path.suffix!='.gz':raise ValueError('Unexpected calibration image path')
            from ownpr_clock_images import image_sha
            if sha(path)!=image['compressed_sha256'] or image_sha(path)!=image['original_sha256']:raise ValueError('Wide FF image changed')
            if image['attributes']!=expected_groups:raise ValueError('Wide FF occupied leaf/FF groups differ from model')
            images[label]=path
        base_payload=payload(images['baseline'])
        if payload(images['warmup'])!=base_payload or payload(images['restored'])!=base_payload:raise ValueError('Wide FF full payload is unstable or unrestored')
        before=projection.read(images['baseline']);after=projection.read(images['linked'])
        req={}
        for leaf in range(1<<(level-4)):req.update(luts.requirements(r,c,leaf,0xA639))
        for g,a in enumerate(expected_groups):req.update(cls.requirements(r,c,g,a))
        if any(int(before[projection.index(b)])-48!=v or int(after[projection.index(b)])-48!=v for b,v in req.items()):
            raise ValueError('Wide FF LUT/CLS configuration mismatch')
        pip=(r,c,'OF'+str(slot),'DI'+str(slot));link=switches.requirements(pip)
        expected={projection.index(b) for b,v in link.items() if int(before[projection.index(b)])-48!=v}
        actual=set(np.flatnonzero(before!=after).tolist())
        if not expected or actual!=expected or any(int(after[projection.index(b)])-48!=v for b,v in link.items()):
            raise ValueError('Wide FF input-select bit delta differs from native table 2')
        observed=[dict(index=i,before=int(before[i])-48,after=int(after[i])-48) for i in sorted(actual)]
        if row['frame_changes']!=observed or row['changed_frame_bits']!=len(actual):raise ValueError('Wide FF delta record changed')
        delays=row['native_wire_delay_by_selector']
        if set(delays)!={'0','1','2'} or any(len(v)!=4 or any(not math.isfinite(x) or x<0 for x in v) for v in delays.values()):
            raise ValueError('Incomplete native wide FF wire-delay queries')
        if row['full_sta_verified'] or row['production_enabled'] or row['hardware_verified']:raise ValueError('Unsupported wide FF validation claim')
        checked.append(dict(tile_type=typ,level=level,slot=slot,ff_profile=profile,pip=list(pip),configuration_requirements=[dict(bit=list(b),value=v) for b,v in link.items()],
            required_logic_bits=len(req),changed_frame_bits=len(actual),native_wire_delay_by_selector=delays))
    if seen!={(t,l,s) for t in (17,228) for l,s in ROOT_SLOT.items()} or set(route_rows)!=seen:
        raise ValueError('Incomplete wide FF calibration cases')
    return dict(status='wide_ff_configuration_and_isolated_wire_queries_matched',records=checked,capture_sha256=meta['encoding_sha256'],
        routes_sha256=meta['routes_sha256'],source_fse_sha256=device.fse.sha256,full_sta_verified=False,production_packer_enabled=False,
        scope='Six explicit root/FF selections, occupied leaf and FF modes, reversible table-2 encoding; isolated wire query is not full path timing.')


class SelectedWideFfGraph(RoutingGraph):
    """Expose only nominated terminal PIPs; never enable all DI fanouts.

    Intended for backend bring-up. A calibrated physical selection is not
    proof that an arbitrary logical FF/macro placement is legal.
    """
    def __init__(self,device,rrg,folder,selections):
        self.calibration=audit_capture(folder)
        super().__init__(device,rrg)
        calibrated={(r['tile_type'],r['level'],r['slot']) for r in self.calibration['records']}
        self.selected_links={};self._link_out={};self._link_in={};occupied=set()
        for row,column,level,slot in selections:
            if not (0<=row<device.rows and 0<=column<device.cols) or (device.grid[row][column],level,slot) not in calibrated:
                raise ValueError('Selected wide FF link lacks calibration')
            if (row,column) in occupied:raise ValueError('Only one calibrated wide FF macro per site')
            occupied.add((row,column));pip=(row,column,'OF'+str(slot),'DI'+str(slot))
            source=self.node(row,column,pip[2]);dest=self.node(row,column,pip[3])
            self.selected_links[pip]=(source,dest);self._link_out[source]=(dest,1.0,pip);self._link_in[dest]=(source,1.0,pip)

    def downhill(self,node):
        return super().downhill(node)+((self._link_out[node],) if node in self._link_out else ())

    def uphill(self,node):
        return super().uphill(node)+((self._link_in[node],) if node in self._link_in else ())

    def check_pip(self,pip):
        return tuple(pip) in self.selected_links or super().check_pip(pip)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);a=p.parse_args();result=audit_capture(a.folder)
    (a.folder/'configuration_audit.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
