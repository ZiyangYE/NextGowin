"""Exclusive fixed-pad output-register packing and capacity checks."""
import copy,json,re
from collections import defaultdict
from ownpr_device import ROOT,DLL_SHA,Device
from ownpr_place import sha
from ownpr_ff_configuration import FF_CONTROLS
from ownpr_ioreg_configuration import output_attributes,output_binding,IoregChoices


def calibration():
    audit=json.loads((ROOT/'results/native_ioreg_configuration_audit_003.json').read_text())
    for key,folder,file,hashkey in [('attributes','native_ioreg_attributes_003','ioreg_attributes.json','attributes_sha256'),
        ('reference','native_uart_ioreg_calibration_003','ioreg_state.json','reference_state_sha256'),
        ('encoding','native_ioreg_attributes_002','ioreg_encoding_probe.json','capture_sha256')]:
        p=ROOT/'results'/folder;meta=json.loads((p/'manifest.json').read_text())
        if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or sha(p/file)!=audit[key][hashkey]:raise ValueError('I/O-register calibration changed')
    if audit['reference']['output_edges']['edges']!=11 or not audit['encoding']['full_payload_restored']:raise ValueError('Incomplete I/O-register reference')
    return audit


def site_for(entry):
    m=re.fullmatch(r'IO([BR])(\d+)([AB])',entry['physical_site'])
    if not m:raise ValueError('Output register requires a calibrated bottom/right I/O site')
    side,index,letter=m.groups();index=int(index)
    return [110,index,ord(letter)-65] if side=='B' else [index,183,ord(letter)-65]


def validate(design,placed,device=None):
    evidence=calibration();device=device or Device();choices=IoregChoices(device);cells=design['modules']['top']['cells'];constants={'0':0,'1':1};loads=defaultdict(list)
    for name,cell in cells.items():
        if cell['type'] in ('GND','VCC'):
            for bit in next(iter(cell['connections'].values())):constants[bit]=int(cell['type']=='VCC')
        for pin,bits in cell['connections'].items():
            if cell['port_directions'][pin]=='input':
                for bit in bits:loads[bit].append((name,pin))
    groups=placed.get('ioreg_clusters',{});core=[r['ff'] for r in placed['clusters'].values() if r['ff']]
    expected={n for n,c in cells.items() if c['type'] in FF_CONTROLS}
    if len(core)!=len(set(core)) or set(core)&set(groups) or set(core)|set(groups)!=expected:raise ValueError('FF placement coverage or ownership mismatch')
    used=set();records=[];preset_model=None
    for name,group in groups.items():
        cell=cells[name];typ=cell['type'];io=group['iobuf']
        if typ not in ('DFFCE','DFFPE','DFFRE') or io not in placed['fixed_io'] or cells[io]['type']!='OBUF':raise ValueError('Uncalibrated output FF/I/O primitive combination')
        if typ=='DFFPE':
            from ownpr_ioreg_timing import IoregTimingModel
            if preset_model is None:preset_model=IoregTimingModel()
            preset_model.ensure_preset_calibration()
        control=FF_CONTROLS[typ];initial='1' if typ=='DFFPE' else '0'
        if any(len(cell['connections'].get(p,[]))!=1 for p in ('D','Q','CLK','CE',control)):raise ValueError('Nonscalar output-register pin')
        init=cell['parameters'].get('INIT',initial)
        if not isinstance(init,str) or not init or set(init)-{'0','1'} or int(init,2)!=int(initial):raise ValueError('Uncalibrated output-register INIT')
        q=cell['connections']['Q'][0]
        for net in design['modules']['top'].get('netnames',{}).values():
            value=net.get('attributes',{}).get('init')
            if value is None or q not in net['bits']:continue
            if not isinstance(value,str) or len(value)!=len(net['bits']) or value[-1-net['bits'].index(q)]!=initial:raise ValueError('Uncalibrated output-register net initialization')
        if loads[q]!=[(io,'I')] or cells[io]['connections']['I']!=[q]:raise ValueError('Output-register Q must exclusively drive its fixed OBUF')
        if any(q in p['bits'] for p in design['modules']['top']['ports'].values()):raise ValueError('Unbuffered output-register port connection')
        static_sync=typ=='DFFRE';control_mode='zero' if static_sync else 'signal'
        if static_sync:
            if constants.get(cell['connections']['CE'][0])!=1 or constants.get(cell['connections'][control][0])!=0:raise ValueError('Synchronous I/O packing requires CE=1 and RESET=0')
            from ownpr_ioreg_timing import IoregTimingModel
            if preset_model is None:preset_model=IoregTimingModel()
            preset_model.ensure_constant_sync_calibration()
        if any(cell['connections'][p][0] in constants or not isinstance(cell['connections'][p][0],int) for p in (('CLK',) if static_sync else ('CLK',control))):
            raise ValueError('Constant output-register controls require separate routing validation')
        ce=cell['connections']['CE'][0];ce_mode='signal'
        if ce in constants:
            if typ not in ('DFFPE','DFFRE') or constants[ce]!=1:raise ValueError('Constant CE mode is not calibrated for this output register')
            ce_mode='one'
        elif not isinstance(ce,int):raise ValueError('Invalid output-register CE source')
        entry=placed['fixed_io'][io]
        if device.pin_site_name(entry['package_pin'])!=entry['physical_site']:raise ValueError('Output register package pin does not match its physical pad')
        site=site_for(entry)
        if group['site']!=site or tuple(site) in used:raise ValueError('Output-register fixed pad changed or block shared')
        used.add(tuple(site));binding=output_binding(site);attrs=output_attributes(typ,ce_mode,control_mode);requirements=choices.requirements(site,attrs)
        extension={};typ_id=device.grid[binding['D'][0]-1][binding['D'][1]-1]
        if typ_id in (245,63):
            from ownpr_ioreg_site_extension import admit
            extension['site_extension_sha256']=admit(typ_id,site,attrs)
        records.append(dict(instance=name,iobuf=io,site=site,pins={**{p:binding[p] for p in ('D','CLK','CE','Q')},control:binding['SR']},attributes=attrs,control_pin=control,ce_mode=ce_mode,control_mode=control_mode,default_initial_value=int(initial),**extension,
            known_configuration_bits=len(requirements),link=dict(source=name,source_pin='Q',destination=io,pin='I')))
    return dict(status='exclusive_output_ioreg_capacity_checked',records=records,capacity_verified=True,
        calibration_sha256=evidence['encoding']['capture_sha256'],constant_controls_supported=False,constant_control_profiles=['DFFRE CE=1 RESET=0'],constant_ce_one_supported_for=['DFFPE','DFFRE'],shared_iologic_supported=False,full_physical_legality_verified=False)


def reserved_points(design,placed):
    """Exclusive output wires and inactive external CE terminals of static modes."""
    cells=design['modules']['top']['cells'];ones={'1'};zeros={'0'}
    for cell in cells.values():
        if cell['type']=='VCC':ones.update(cell['connections']['V'])
        if cell['type']=='GND':zeros.update(cell['connections']['G'])
    points=set()
    for name,group in placed.get('ioreg_clusters',{}).items():
        points.add(tuple(placed['fixed_io'][group['iobuf']]['native_ports']['in']))
        if cells[name]['connections']['CE'][0] in ones:points.add(tuple(output_binding(group['site'])['CE']))
        if cells[name]['type']=='DFFRE' and cells[name]['connections']['RESET'][0] in zeros:points.add(tuple(output_binding(group['site'])['SR']))
    return points


def relocate(design,placed,ports):
    result=copy.deepcopy(placed);cells=design['modules']['top']['cells'];drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    selected={entry['port']:n for n,entry in placed['fixed_io'].items() if entry['port'] in ports}
    if set(selected)!=set(ports) or len(set(ports))!=len(ports):raise ValueError('Requested output port missing or duplicated')
    changes=[]
    for port,io in selected.items():
        if cells[io]['type']!='OBUF':raise ValueError('Requested port is not a scalar output buffer')
        ff=drivers.get(cells[io]['connections']['I'][0]);groups=[(n,r) for n,r in result['clusters'].items() if r['ff']==ff]
        if ff not in cells or cells[ff]['type'] not in FF_CONTROLS or len(groups)!=1:raise ValueError('Output is not driven by one paired core FF')
        name,cluster=groups[0];old=[cluster['row'],cluster['column'],cluster['slot']];cluster['ff']=None
        result.setdefault('ioreg_clusters',{})[ff]=dict(iobuf=io,site=site_for(placed['fixed_io'][io]))
        changes.append(dict(ff=ff,port=port,source_lut=name,old_core_site=old,new_iob_site=result['ioreg_clusters'][ff]['site']))
    check=validate(design,result);result.update(physical_legality_complete=False,timing_signoff=False)
    return result,changes,check
