"""Emit an explicitly partial configuration for the initial own P&R slice.

Combines native FSE LUT truth bits and ordinary PIP choices. Missing primitive
modes, defaults, clocks and frame integrity prohibit emitting a usable .fs.
"""
import argparse,hashlib,json
from collections import defaultdict
from pathlib import Path
from ownpr_device import Device,ROOT
from ownpr_fuse_audit import SwitchChoices
from ownpr_lut_configuration import LutInitChoices
from ownpr_frame_integrity import HEADER_SHA256
from ownpr_ff_configuration import compile_initial
from ownpr_cls_configuration import ClsChoices


def build(folder,route_folder):
    folder=Path(folder);route_folder=Path(route_folder)
    packed=folder/'packed.json';placed=folder/'placement.json';routes=route_folder/'routes.json'
    netlist=json.loads(packed.read_text());placement=json.loads(placed.read_text());routing=json.loads(routes.read_text())
    cells=netlist['modules']['top']['cells'];device=Device();luts=LutInitChoices(device);switches=SwitchChoices(device)
    ff_configuration=compile_initial(netlist,placement);cls=ClsChoices(device)
    wide_configuration=None
    ioreg_configuration=None
    if placement.get('ioreg_clusters'):
        from ownpr_ioreg_macros import validate as validate_ioreg
        ioreg_configuration=validate_ioreg(netlist,placement,device)
    if placement.get('mux_clusters'):
        from ownpr_mux_configuration import compile_wide
        wide_configuration=compile_wide(netlist,placement,device)
    calibration_path=ROOT/'results/native_device_lut_004/lut_encoding_audit.json'
    calibration=json.loads(calibration_path.read_text())
    if (calibration.get('status')!='verified_sample_lut_init_mapping' or calibration.get('source_fse_sha256')!=device.fse.sha256
            or calibration.get('slots')!=list(range(8)) or not calibration.get('complete_bit_index_patterns')
            or not all(c['match'] for c in calibration['checks'])):raise ValueError('Native LUT calibration is incomplete or incompatible')
    calibrated_types=set(calibration['tile_types'])
    pip_calibration_path=ROOT/'results/native_pip_encoding_010/pip_encoding_audit.json'
    pip_calibration=json.loads(pip_calibration_path.read_text())
    if pip_calibration.get('status')!='verified_raw_pip_writes' or pip_calibration.get('source_fse_sha256')!=device.fse.sha256:
        raise ValueError('Incompatible raw PIP encoding calibration')
    calibrated_pips={tuple(key) for key in pip_calibration['calibrated_keys']};uncalibrated_pips=set()
    values=defaultdict(dict);conflicts=[];coverage=defaultdict(int)
    def add(requirements,owner):
        for bit,value in requirements.items():
            if values[bit] and value not in values[bit]:conflicts.append(dict(bit=bit,owners=list(values[bit].values())+[owner]))
            values[bit][value]=owner
    for cluster in placement['clusters'].values():
        cell=cells[cluster['lut']];kind=cell['type']
        if kind not in ('LUT1','LUT2','LUT3','LUT4'):raise ValueError('LUT primitive not supported by initial encoder')
        width=int(kind[-1]);text=cell['parameters']['INIT']
        if not isinstance(text,str) or set(text)-{'0','1'}:raise ValueError('Unknown LUT truth value')
        init=int(text,2)
        if init >= 1<<(1<<width):raise ValueError('LUT INIT exceeds primitive width')
        physical=sum(((init>>(a&((1<<width)-1)))&1)<<a for a in range(16))
        r,c,slot=cluster['row'],cluster['column'],cluster['slot'];coverage[device.grid[r][c]]+=1
        add(luts.requirements(r,c,slot,physical),'lut:'+cluster['lut'])
    for name,net in routing['nets'].items():
        for pip in net['pips']:
            add(switches.requirements(pip),'net:'+name)
            r,c,src,dst=pip;key=(device.grid[r][c],device.ids[src],device.ids[dst])
            if key not in calibrated_pips:uncalibrated_pips.add(key)
    for site in ff_configuration['sites']:
        for group,attrs in enumerate(site['groups']):
            add(cls.requirements(site['row'],site['column'],group,attrs),f'cls:{site["row"]}:{site["column"]}:{group}')
    if wide_configuration:
        for site in wide_configuration['sites']:
            for group,attrs in enumerate(site['groups']):add(cls.requirements(site['row'],site['column'],group,attrs),f'wide_cls:{site["row"]}:{site["column"]}:{group}')
    if ioreg_configuration:
        from ownpr_ioreg_configuration import IoregChoices
        ioreg_choices=IoregChoices(device)
        for record in ioreg_configuration['records']:add(ioreg_choices.requirements(record['site'],record['attributes']),'ioreg:'+record['instance'])
    arithmetic_info=None
    if placement.get('arithmetic_clusters'):
        from ownpr_alu_configuration import Mode2Configuration
        from ownpr_carry_macros import validate as validate_carry
        arithmetic_info=validate_carry(netlist,placement,device);model=Mode2Configuration(device);by_site=defaultdict(list)
        for name,loc in placement['arithmetic_clusters'].items():by_site[loc['row'],loc['column']].append((name,loc))
        for (r,c),members in by_site.items():
            from ownpr_carry_lut_sharing import attributes
            attrs,shared=attributes(placement,r,c,[loc['slot'] for _,loc in members])
            if shared:
                for group,values_ in enumerate(attrs):add(cls.requirements(r,c,group,values_),f'mixed_carry_cls:{r}:{c}:{group}')
            for name,loc in members:
                add(model.requirements(r,c,loc['slot'],loc['mode'],loc['input_profile'],attrs[loc['slot']//2]),'alu:'+name)
    return dict(status='partial_configuration_conflicting' if conflicts else 'partial_configuration_consistent',
        source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (packed,placed,routes)},
        open_chip_database_used=False,vendor_pnr_executed=False,lut_counts_by_tile_type=dict(coverage),
        lut_encoder_native_calibration_tile_types=sorted(calibrated_types),uncalibrated_lut_tile_types=sorted(set(coverage)-calibrated_types),
        lut_calibration_sha256=hashlib.sha256(calibration_path.read_bytes()).hexdigest(),
        frame_codec_calibrated_header_sha256=HEADER_SHA256,
        raw_pip_writes_calibrated=not uncalibrated_pips,uncalibrated_pip_keys=sorted(uncalibrated_pips),
        pip_calibration_sha256=hashlib.sha256(pip_calibration_path.read_bytes()).hexdigest(),
        ff_configuration=ff_configuration,
        arithmetic_configuration=arithmetic_info,
        wide_mux_configuration=wide_configuration,
        ioreg_configuration=ioreg_configuration,
        bits=[dict(tile=[r,c],local_bit=[br,bc],value=next(iter(v))) for (r,c,br,bc),v in sorted(values.items()) if len(v)==1],
        conflicts=conflicts,full_bitstream_verified=False,timing_signoff=False,
        pending=['complete packed-design mode/route validation','logical-node to physical-switch mapping and defaults','special clock/LW configuration',
                 'IOB/bank/default/GSR configuration','global header/footer/checksum commands and full bitstream emission','complete setup/hold and IO timing'],
        scope='Partial native FSE configuration requirements with raw PIP write calibration. High-level node mapping and complete defaults remain unverified. Not a hardware image.')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--placement',type=Path,required=True);ap.add_argument('--routes',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args();result=build(a.placement,a.routes);a.out.write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('bits','conflicts')},indent=2));print('bits',len(result['bits']),'conflicts',len(result['conflicts']))
