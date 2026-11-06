"""Explicitly admitted ordinary LUT/FF pairs sharing identical controls.

Only the type-17 DFFRE, default-init, RESET=0, CE=1/dynamic slice has combined
encoding witnesses. No carry/wide sharing or unconstrained control merging.
"""
import hashlib,json
from collections import defaultdict
from functools import lru_cache
from ownpr_device import ROOT,DLL_SHA
from ownpr_image_storage import load_json,sha


@lru_cache(maxsize=1)
def calibration():
    from ownpr_shared_ff_calibration import audit
    from ownpr_clock_images import image_sha
    paired=audit(ROOT/'results/native_ff_shared_paired_001')
    if not paired['paired_lut_data_verified'] or paired['tile_type']!=17:raise ValueError('Shared paired-data calibration incomplete')
    records=[]
    for ce in ('one','signal'):
        for slot in (0,2,4):
            name='native_shared_ff_re_one_zero_001' if (ce,slot)==('one',0) else f'native_shared_ff_re_{ce}_zero_slot{slot}_001'
            f=ROOT/'results'/name;m=load_json(f/'manifest.json');a=load_json(f/'encoding_audit.json')
            if m['status']!='captured' or m['dll_sha256']!=DLL_SHA or m['vendor_placement_executed'] or m['vendor_routing_executed'] or m['open_chip_database_used']:raise ValueError('Shared encoding provenance invalid')
            if sha(f/'encoding_audit.json')!=m['encoding_audit_sha256']:raise ValueError('Shared encoding audit changed')
            for file,digest in m['source_sha256'].items():
                if sha(f/file)!=digest:raise ValueError('Shared encoding source changed')
            expected=dict(cell_type='DFFRE',slot=slot,ce=ce,control='zero',clock='signal')
            if a['status']!='shared_pair_configuration_matched' or a['profile']!=expected or a['slots']!=[slot,slot+1] or a['tile_type']!=17:raise ValueError('Shared encoding profile differs')
            if not a['full_payload_restored'] or not a['attributes_matched'] or a['actual_changes']!=a['expected_changes'] or any(a[k] for k in ('missing','unexpected','value_mismatches','baseline_mismatches')):raise ValueError('Shared encoding comparison failed')
            for label in ('baseline','variant','restored'):
                row=a[label]
                if sha(f/row['file'])!=row['compressed_sha256'] or image_sha(f/row['file'])!=row['original_sha256']:raise ValueError('Shared encoding image changed')
            records.append(dict(folder=name,manifest_sha256=sha(f/'manifest.json'),audit_sha256=sha(f/'encoding_audit.json'),fse_sha256=a['source_fse_sha256'],profile=expected))
    if len({r['fse_sha256'] for r in records})!=1:raise ValueError('Shared calibration FSE differs')
    evidence=dict(records=records,paired_data_sources=paired['source_sha256'])
    return dict(evidence,admission_sha256=hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest(),fse_sha256=records[0]['fse_sha256'])


def constants(design):
    result={'0':'zero','1':'one'}
    for c in design['modules']['top']['cells'].values():
        if c['type'] in ('GND','VCC'):
            for bs in c['connections'].values():
                for b in bs:result[b]='one' if c['type']=='VCC' else 'zero'
    return result


def signature(cell,constant):
    if cell['type']!='DFFRE' or set(cell.get('parameters',{}))-{'INIT'}:return None
    init=cell.get('parameters',{}).get('INIT','0')
    if not isinstance(init,str) or not init or set(init)-{'0','1'} or int(init,2)!=0:return None
    pins=cell['connections']
    if any(len(pins.get(p,[]))!=1 for p in ('CLK','CE','RESET','D','Q')):return None
    if constant.get(pins['RESET'][0])!='zero' or pins['CLK'][0] in constant or constant.get(pins['CE'][0])=='zero':return None
    # Both dynamic controls and constant networks must have the same identity.
    # Classifying two different nets as "signal" never makes them compatible.
    return tuple(pins[p][0] for p in ('CLK','CE','RESET'))


def compile_sites(design,placement,device):
    from ownpr_ff_configuration import independent_ff_groups,single_ff_attributes,FF_CONTROLS
    evidence=calibration()
    if placement.get('shared_ff_calibration')!=evidence['admission_sha256'] or device.fse.sha256!=evidence['fse_sha256']:raise ValueError('Shared FF packing lacks exact calibration admission')
    cells=design['modules']['top']['cells'];const=constants(design);sites=defaultdict(list);seen=set();ffs=set();result={};records=[]
    exclusive={(r['row'],r['column']) for key in ('arithmetic_clusters','mux_clusters') for r in placement.get(key,{}).values()}
    for row in placement['clusters'].values():
        point=row['row'],row['column'],row['slot']
        if point in seen:raise ValueError('Shared packing contains duplicate LUT slot')
        seen.add(point)
        if row.get('ff'):
            if row['ff'] in ffs:raise ValueError('Shared packing contains duplicate FF')
            ffs.add(row['ff']);sites[point[:2]].append(row)
    for site,members in sites.items():
        by_group=defaultdict(list)
        for row in members:by_group[row['slot']//2].append(row)
        sharing=any(len(rows)>1 for rows in by_group.values())
        if not sharing:
            if any(row['slot'] not in (0,2,4) and not row.get('wide_ff_root') for row in members):raise ValueError('Orphan odd-slot register is not admitted')
            continue
        r,c=site
        if site in exclusive or not 0<=r<device.rows or not 0<=c<device.cols or device.grid[r][c]!=17:raise ValueError('Shared FFs overlap an uncalibrated macro/site')
        groups=independent_ff_groups([])
        for group,rows in sorted(by_group.items()):
            rows=sorted(rows,key=lambda row:row['slot']);base=group*2
            if base not in (0,2,4) or len(rows)>2 or rows[0]['slot']!=base:raise ValueError('Uncalibrated shared control slots')
            profiles=[]
            for row in rows:
                cell=cells[row['ff']];lut=cells[row['lut']]
                if row.get('wide_ff_root') or lut['type'] not in ('LUT1','LUT2','LUT3','LUT4') or cell['connections']['D']!=lut['connections']['F']:raise ValueError('Shared FF data must use its paired ordinary LUT')
                typ=cell['type']
                if typ not in FF_CONTROLS:raise ValueError('Uncalibrated FF type')
                profile=dict(cell_type=typ,slot=row['slot'],ce=const.get(cell['connections']['CE'][0],'signal'),control=const.get(cell['connections'][FF_CONTROLS[typ]][0],'signal'),clock='signal')
                profiles.append(profile)
            if len(rows)==2:
                signatures=[signature(cells[row['ff']],const) for row in rows]
                if signatures[0] is None or signatures[0]!=signatures[1] or rows[1]['slot']!=base+1:raise ValueError('Shared FF controls or initialization are incompatible')
                if not any(r['profile']==profiles[0] for r in evidence['records']):raise ValueError('No combined shared FF profile witness')
                from ownpr_shared_ff_calibration import predict
                attrs=predict(profiles)[group]
                records.append(dict(tile=list(site),slots=[base,base+1],ffs=[row['ff'] for row in rows],luts=[row['lut'] for row in rows],control_bits=list(signatures[0]),profile=profiles[0]))
            else:attrs=single_ff_attributes(**profiles[0])[group]
            groups[group]=attrs
        result[site]=groups
    if not records:raise ValueError('Shared FF admission has no actual pairs')
    return result,dict(records=records,calibration_sha256=evidence['admission_sha256'],full_routing_sta_required=True)
