"""Independent positive-edge FF control attributes for GW5AST-138.

Supports SDK DFFRE/DFFSE/DFFCE/DFFPE with constant, normal or inverted CE/SR
and normal/inverted clock. Fixtures cover standalone/paired LUT data and
independent DFFRE control groups with default INIT. Nondefault INIT, shared
physical controls and complete hardware legality remain separate requirements.
"""
import argparse,hashlib,json
from pathlib import Path
from ownpr_device import DLL_SHA

FF_CONTROLS={'DFFRE':'RESET','DFFSE':'SET','DFFCE':'CLEAR','DFFPE':'PRESET'}


def single_ff_attributes(cell_type,slot,ce,control,clock='signal'):
    if cell_type not in ('DFFRE','DFFSE','DFFCE','DFFPE'):raise ValueError('Unverified FF primitive')
    if slot not in range(8):raise ValueError('Invalid native FF slot')
    sources={'zero','one','signal','inverted'}
    if ce not in sources or control not in sources or clock not in {'signal','inverted'}:raise ValueError('Unverified FF control source')
    groups=[[0,0,15,5,1,2,0,2,0,2,0,2,2,17,17,0,0,0,0] for _ in range(4)]
    groups[0][15]=21
    attributes=groups[slot//2]
    attributes[0]=9;attributes[1]=12
    attributes[2]=14 if cell_type in ('DFFCE','DFFPE') else 15
    attributes[4]=7
    attributes[5:7]={'zero':[1,0],'one':[2,0],'signal':[0,3],'inverted':[0,4]}[ce]
    attributes[7:9]=[0,3 if clock=='signal' else 4]
    attributes[9:11]={'zero':[1,0],'one':[2,0],'signal':[2,3],'inverted':[2,4]}[control]
    attributes[13+(slot&1)]=16 if cell_type in ('DFFSE','DFFPE') else 17
    return groups


def audit(folder):
    folder=Path(folder);manifest=json.loads((folder/'manifest.json').read_text());path=folder/'ff_attributes.json'
    if (manifest.get('status')!='captured' or manifest.get('dll_sha256')!=DLL_SHA or manifest.get('open_chip_database_used') is not False
            or manifest.get('vendor_placement_executed') is not False or manifest.get('vendor_routing_executed') is not False
            or hashlib.sha256(path.read_bytes()).hexdigest()!=manifest.get('attributes_sha256')):raise ValueError('Unverified FF semantic capture')
    data=json.loads(path.read_text());keys=set();mismatches=[];paired={bool(r.get('paired_lut')) for r in data['records']}
    paired_verified=paired=={True} and all(r.get('native_direct_connection') is True and r.get('wrong_slot_direct_connection') is False
        and r.get('native_data_route_nodes')==0 for r in data['records'])
    labels={'zero':0,'one':1,'signal':2,'inverted':3}
    for row in data['records']:
        key=(row['cell_type'],row['slot'],row['ce'],row['control'],row.get('clock','signal'))
        if key in keys:raise ValueError('Duplicate FF calibration case')
        keys.add(key)
        if row['native_ce_class']!=labels[row['ce']] or row['native_control_class']!=labels[row['control']]:raise ValueError('Native control source classification mismatch')
        predicted=single_ff_attributes(*key)
        if predicted!=row['attributes']:mismatches.append(dict(case=key,predicted=predicted,observed=row['attributes']))
    complete=len(keys)==4*8*4*4*2 and (paired!={True} or paired_verified)
    multi_mismatches=[]
    for i,row in enumerate(data.get('multi_records',[])):
        if independent_ff_groups(row['ffs'])!=row['attributes']:multi_mismatches.append(i)
    return dict(status='verified_single_ff_controls' if complete and not mismatches and not multi_mismatches else 'failed',cases=len(keys),
        control_source_and_slot_coverage_complete=complete,mismatch_count=len(mismatches),mismatches=mismatches[:12],
        default_init_only=True,standalone_ff_only=paired=={False},paired_lut_data_verified=paired_verified,shared_control_legality_verified=False,
        independent_group_cases=len(data.get('multi_records',[])),independent_group_mismatch_indices=multi_mismatches,
        full_bitstream_verified=False,source_attributes_sha256=manifest['attributes_sha256'],
        scope='Independent FF control attributes and optional native direct-connection predicate checks. Arbitrary INIT, shared physical controls and hardware signoff remain unverified.')


def independent_ff_groups(ffs):
    """Combine the initial packer's one-FF-per-independent-control-group slice."""
    groups=single_ff_attributes('DFFRE',0,'one','zero')
    groups[0]=[0,0,15,5,1,2,0,2,0,2,0,2,2,17,17,21,0,0,0]
    occupied=set()
    for ff in ffs:
        slot=ff['slot']
        if slot not in (0,2,4) or slot in occupied:raise ValueError('Unverified shared FF control group')
        occupied.add(slot)
        attrs=single_ff_attributes(ff['cell_type'],slot,ff['ce'],ff['control'],ff.get('clock','signal'))
        groups[slot//2]=attrs[slot//2]
    return groups


def compile_initial(design,placement,top='top'):
    """Compile current LUT1-4/FF pairs with independent control groups."""
    cells=design['modules'][top]['cells'];constants={'0':0,'1':1};sites={};static=[];pairs=[]
    wide={}
    if any(r.get('wide_ff_root') for r in placement['clusters'].values()):
        from ownpr_wide_ff_packing import pairs as wide_pairs
        from ownpr_device import Device
        wide={r['ff']:r for r in wide_pairs(design,placement,Device())}
    for cell in cells.values():
        if cell['type'] in ('VCC','GND'):
            pin='V' if cell['type']=='VCC' else 'G';constants[cell['connections'][pin][0]]=int(cell['type']=='VCC')
    def source(cell,pin):
        bits=cell['connections'][pin]
        if len(bits)!=1:raise ValueError('Non-scalar FF control')
        bit=bits[0]
        if bit in constants:return 'one' if constants[bit] else 'zero'
        if not isinstance(bit,int):raise ValueError('Undefined FF control')
        return 'signal'
    control_names=FF_CONTROLS
    for cluster in placement['clusters'].values():
        name=cluster['ff']
        if not name:continue
        cell=cells[name];typ=cell['type'];lut=cells[cluster['lut']]
        if typ not in control_names or lut['type'] not in ('LUT1','LUT2','LUT3','LUT4'):raise ValueError('Unverified FF/LUT pairing primitive')
        if name not in wide and cell['connections']['D']!=lut['connections']['F']:raise ValueError('FF data is not its paired LUT output')
        default=int(typ in ('DFFSE','DFFPE'));init=cell.get('parameters',{}).get('INIT',str(default))
        if not isinstance(init,str) or set(init)-{'0','1'} or int(init,2)!=default:raise ValueError('Nondefault FF INIT has not been calibrated')
        if source(cell,'CLK')!='signal':raise ValueError('Constant FF clock not supported by initial configuration')
        control=control_names[typ];profile=dict(cell_type=typ,slot=cluster['slot'],ce=source(cell,'CE'),control=source(cell,control),clock='signal')
        key=cluster['row'],cluster['column'];sites.setdefault(key,[]).append(profile)
        if name not in wide:pairs.append(dict(lut=cluster['lut'],ff=name,tile=list(key),slot=cluster['slot'],native_internal_connection_verified=True))
        for pin in ('CE',control):
            label=source(cell,pin)
            if label in ('zero','one'):static.append(dict(cell=name,pin=pin,value=int(label=='one'),configuration_mode_verified=True,bitstream_verified=False))
    wide_sites={(v['row'],v['column']):v['groups'] for v in wide.values()}
    shared_sites={};shared_info=None
    if placement.get('shared_ff_calibration'):
        from ownpr_shared_ff_packing import compile_sites
        from ownpr_device import Device
        shared_sites,shared_info=compile_sites(design,placement,Device())
    result=dict(sites=[dict(row=r,column=c,groups=wide_sites[r,c] if (r,c) in wide_sites else shared_sites[r,c] if (r,c) in shared_sites else independent_ff_groups(ffs)) for (r,c),ffs in sorted(sites.items())],
        static_control_pins=static,paired_data_connections=pairs,default_init_only=True,full_bitstream_verified=False)
    if shared_info:result['shared_control_pairs']=shared_info
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);a=ap.parse_args();result=audit(a.folder)
    (a.folder/'ff_semantic_audit.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)
