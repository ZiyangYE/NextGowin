"""Native-derived output I/O-register control attributes and FSE predicates.

Positive-edge DFFRE/SE/CE/PE only. This model does not certify I/O clock
selection, shared I/O logic, alternate INIT, or complete bitstream validity.
"""
from ownpr_image_storage import read_text as read_artifact_text
import argparse,json,struct
import numpy as np
from pathlib import Path
from ownpr_device import Device,ROOT,DLL_SHA
from ownpr_place import sha
from ownpr_fuse_audit import SwitchChoices
from ownpr_lut_configuration import NativeFrameProjection
from audit_native_encoding_probe import payload


def output_attributes(cell_type,ce,control,clock='signal'):
    if cell_type not in ('DFFRE','DFFSE','DFFCE','DFFPE'):raise ValueError('Unsupported output register primitive')
    sources={'zero','one','signal','inverted'}
    if ce not in sources or control not in sources or clock not in {'signal','inverted'}:raise ValueError('Unsupported output register control source')
    a=[0]*171
    for i,v in {1:49,2:52,5:61,8:4,10:2,11:4,12:2,15:4,19:1,20:1,21:1,22:2,27:53,28:56,57:61,58:61}.items():a[i]=v
    if cell_type in ('DFFCE','DFFPE'):a[2]=0
    if cell_type in ('DFFSE','DFFPE'):a[28]=55
    a[10],a[50]={'zero':(1,0),'one':(2,0),'signal':(0,4),'inverted':(0,3)}[ce]
    a[19],a[21]={'zero':(1,1),'one':(1,0),'signal':(4,0),'inverted':(3,0)}[control]
    a[15]=4 if clock=='signal' else 3
    return a


def output_binding(site):
    r,c,block=site
    if block not in (0,1):raise ValueError('Invalid output I/O block')
    # Calibrated bottom and right edges. Other edge modes remain unsupported.
    if r==110 and 0<c<183:row,column=109,c+block
    elif c==183 and 0<r<110:row,column=r+block,182
    else:raise ValueError('Uncalibrated output I/O edge')
    if not 0<row<110 or not 0<column<183:raise ValueError('Output I/O endpoint outside the ordinary grid')
    return dict(D=[row,column,10 if block else 7],CLK=[row,column,124],CE=[row,column,130],SR=[row,column,127],Q=None)


class IoregChoices:
    def __init__(self,device):
        self.device=device;self.fuses=SwitchChoices(device)
        self.features=list(device.fse.read_rows(device.fse.get_table('header',0xa)));self.templates={}

    def requirements(self,site,attributes):
        node=output_binding(site)['D'];typ=self.device.grid[node[0]-1][node[1]-1]
        if typ not in (49,246,247,248):
            from ownpr_ioreg_site_extension import admit
            admit(typ,site,attributes)
        return self.observed_requirements(site,attributes)

    def observed_requirements(self,site,attributes):
        """Readback of captured attributes; does not admit a new placement mode."""
        if len(attributes)!=171:raise ValueError('Invalid I/O-logic attribute vector')
        if attributes[0]!=0 or attributes[1]!=49:raise ValueError('Uncalibrated output I/O mode')
        node=output_binding(site)['D'];r,c=node[0]-1,node[1]-1;typ=self.device.grid[r][c];kind=0x15+site[2]
        key=typ,kind
        if key not in self.templates:
            table=self.device.fse.get_table(str(typ),kind)
            if table is None:raise ValueError('Native output I/O feature table missing')
            self.templates[key]=[(row[:2],tuple(self.fuses.bit(typ,i) for i in row[2:] if i>=0)) for row in self.device.fse.read_rows(table)]
        mask=set();ones=set()
        for predicates,bits in self.templates[key]:
            mask.update(bits);selected=bool(attributes[0] or attributes[1])
            for predicate in predicates:
                if not predicate:continue
                if abs(predicate)>=len(self.features):raise ValueError('Invalid I/O feature predicate')
                attr,value,_=self.features[abs(predicate)]
                if not 0<=attr<len(attributes):raise ValueError('I/O attribute outside modeled vector')
                selected &= (attributes[attr]==value)==(predicate>0)
            if selected:ones.update(bits)
        if len(mask)!=100:raise ValueError('Unexpected output I/O configuration mask')
        return {(r,c,*bit):int(bit in ones) for bit in mask}


def audit_attributes(folder):
    folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());path=folder/'ioreg_attributes.json'
    if (meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['vendor_placement_executed'] or meta['vendor_routing_executed'] or meta['open_chip_database_used']
        or sha(path)!=meta['attributes_sha256']):raise ValueError('Unverified output I/O-register capture')
    rows=json.loads(path.read_text())['records'];seen=set();sites={(110,135,0),(110,144,0),(110,144,1),(109,183,0),(103,183,1)}
    for r in rows:
        site=tuple(r['site']);key=site,r['cell_type'],r['ce'],r['control'],r['clock']
        if key in seen or site not in sites:raise ValueError('Duplicate/unexpected I/O-register calibration case')
        seen.add(key)
        if r['binding']!=output_binding(site) or r['attributes']!=output_attributes(*key[1:]):raise ValueError('Independent I/O-register model mismatch')
    if len(seen)!=5*4*4*4*2:raise ValueError('Incomplete I/O-register control coverage')
    return dict(status='output_ioreg_attributes_and_endpoints_matched',cases=len(seen),sites=[list(s) for s in sorted(sites)],
        attributes_sha256=meta['attributes_sha256'],output_direct_link_verified=False,clock_configuration_verified=False,full_bitstream_verified=False)


def audit_reference(folder):
    folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());file=folder/'ioreg_state.json'
    if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or not meta['output_iob_registers'] or not meta['vendor_placement_executed'] or not meta['vendor_routing_executed'] or meta['own_pnr_result']:
        raise ValueError('Unverified native I/O-register reference')
    if sha(file)!=meta['ioreg_state_sha256']:raise ValueError('Reference I/O-register state changed')
    data=json.loads(file.read_text());regs={r['instance']:r for r in data['registers_and_iologic']};device=Device();choices=IoregChoices(device);projection=NativeFrameProjection(device);image=projection.read(folder/'probe.fs');records=[]
    for block in data['iob_blocks']:
        for row in block['iologic']:
            if not row['reg_refs']:continue
            if row['block_offset']!=0x28 or len(row['reg_refs'])!=1 or row['reg_refs'][0]['offset']!=0x818:raise ValueError('Unmodeled shared I/O logic')
            reg=regs[row['reg_refs'][0]['instance']];site=block['site'];binding=output_binding(site)
            if reg['physical_header']!=[0,site[0],site[1],1] or not reg['physical_base_logic_matches'] or not reg['typed_and_base_match']:raise ValueError('I/O-register physical binding changed')
            for pin in reg['pins']:
                label=pin['pin'].split('.',1)[1];key='SR' if label in ('RESET','SET','CLEAR','PRESET') else label
                if pin['raw_node']!=binding[key]:raise ValueError('Reference output I/O endpoint mismatch')
            attrs=row.get('attributes')
            if attrs is None:attrs=list(struct.unpack('<171h',bytes.fromhex(row['tail_810_998'])[48:390]))
            requirements=choices.requirements(site,attrs)
            if any(int(image[projection.index(bit)])-48!=value for bit,value in requirements.items()):raise ValueError('I/O register FSE bits differ from encoded reference')
            records.append(dict(instance=reg['instance'],site=site,required_bits=len(requirements),matched=True))
    if len(records)!=11:raise ValueError('Incomplete fixed-UART output-register reference')
    edges=data.get('output_register_edges');edge_checks=None;async_checks=None
    if edges is not None:
        bysource={e['source']:e for e in edges}
        if len(bysource)!=len(edges) or set(bysource)!={r['instance'] for r in records}:raise ValueError('Incomplete output-register timing edges')
        pairs={}
        for record in records:
            edge=bysource[record['instance']];site=record['site'];block=next(b for b in data['iob_blocks'] if b['site']==site)
            if (edge['destination']!=block['iobuf'] or edge['source_site']!=[0,site[0],site[1],1] or edge['destination_site']!=[0,*site]
                or not edge['timing_edge_present'] or not edge['source_has_timing_node'] or not edge['destination_has_timing_node']
                or edge['minimum_ns']!=0 or edge['maximum_ns']!=0):raise ValueError('Output-register direct timing edge mismatch')
            pairs[edge['destination']]=edge['source']
        from extract_timing import ROW
        previous=None;observed=set();count=0
        for line in read_artifact_text(folder/'probe.tr').splitlines():
            if line.startswith(('Data Arrival Path:','Data Required Path:','Path Statistics:')):previous=None
            m=ROW.match(line)
            if not m:continue
            _,delay,kind,_,_,_,endpoint=m.groups();instance,_,pin=endpoint.rpartition('/')
            if kind=='tNET' and instance in pairs and pin=='I':
                if previous!=(pairs[instance],'Q') or float(delay)!=0:raise ValueError('Output-register report link mismatch')
                count+=1;observed.add(instance)
            previous=(instance,pin)
        if observed!=set(pairs):raise ValueError('Incomplete direct output paths in report')
        edge_checks=dict(edges=len(edges),report_rows=count,matched=True,generic_direct_predicate_is_sufficient=False)
        if (folder/'extended_timing_queries.json').exists():
            from ownpr_extended_timing_model import report_checks
            queryfile=folder/'extended_timing_queries.json'
            if sha(queryfile)!=meta['extended_timing_sha256']:raise ValueError('I/O-register async timing capture changed')
            query=json.loads(queryfile.read_text());async_checks=report_checks(read_artifact_text(folder/'probe.tr'),query)
            async_checks['io_registers_captured']=sum(r['site'][0]==0 for r in query['async_ff'])
    return dict(status='reference_output_ioreg_bits_matched',records=records,required_bits=sum(r['required_bits'] for r in records),
        output_edges=edge_checks,async_query_report_audit=async_checks,complete_ioreg_timing_model_verified=False,
        reference_state_sha256=sha(file),bitstream_sha256=sha(folder/'probe.fs'),source_fse_sha256=device.fse.sha256,
        own_pnr_result=False,full_bitstream_verified=False,scope='Eleven native reference output registers; known I/O-register bits only, no own-P&R result.')


def audit_encoding(folder):
    folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());file=folder/'ioreg_encoding_probe.json';data=json.loads(file.read_text())
    if (meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['vendor_placement_executed'] or meta['vendor_routing_executed'] or meta['open_chip_database_used']
        or sha(file)!=meta.get('encoding_probe_sha256') or data['status']!='isolated_output_ioreg_encoding_matched'):raise ValueError('Unverified output-register encoding probe')
    device=Device();choices=IoregChoices(device);projection=NativeFrameProjection(device)
    if data['source_fse_sha256']!=device.fse.sha256:raise ValueError('Output-register encoding data changed')
    for key in ('baseline','restored'):
        if sha(folder/data[key])!=data[key+'_sha256']:raise ValueError('Output-register encoding payload changed')
    if payload(folder/data['baseline'])!=payload(folder/data['restored']):raise ValueError('Output-register full payload restoration failed')
    base=projection.read(folder/data['baseline']);base_req=choices.requirements(data['site'],output_attributes(data['cell_type'],**data['baseline_profile']));rows=[];seen=set()
    if any(base[projection.index(bit)]-48!=value for bit,value in base_req.items()):raise ValueError('Output-register baseline bits mismatch')
    for record in data['records']:
        profile=record['profile'];key=profile['ce'],profile['control'],profile['clock']
        if key in seen or not record['full_payload_restored'] or sha(folder/record['variant'])!=record['variant_sha256']:raise ValueError('Invalid output-register differential capture')
        seen.add(key);image=projection.read(folder/record['variant']);required=choices.requirements(data['site'],output_attributes(data['cell_type'],**profile))
        predicted={projection.index(bit) for bit,value in required.items() if value!=base_req[bit]};actual=set(np.flatnonzero(base!=image).tolist())
        if actual!=predicted or any(image[projection.index(bit)]-48!=value for bit,value in required.items()):raise ValueError('Output-register differential bits mismatch')
        observed=[dict(index=i,before=int(base[i])-48,after=int(image[i])-48) for i in sorted(actual)]
        if observed!=record['changes'] or len(actual)!=record['changed_bits'] or len(predicted)!=record['expected_bits']:raise ValueError('Stored output-register delta differs from payload')
        rows.append(dict(profile=profile,matched=True,changed_bits=len(actual),full_payload_restored=True))
    expected={(x,'signal','signal') for x in ('zero','one','inverted')}|{('signal',x,'signal') for x in ('zero','one','inverted')}|{('signal','signal','inverted')}
    if seen!=expected:raise ValueError('Incomplete output-register differential controls')
    return dict(status='output_ioreg_differential_bits_matched',site=data['site'],required_bits=len(base_req),records=rows,
        source_fse_sha256=device.fse.sha256,capture_sha256=sha(file),full_payload_restored=True,
        route_control_semantics_verified=False,output_link_verified=False,full_bitstream_verified=False,
        scope='Independent reconstruction of seven control-bit deltas. Constant-pin routing, direct output link and clocks are separate gates.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--attributes',type=Path,required=True);p.add_argument('--reference',type=Path,required=True);p.add_argument('--encoding',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    result=dict(attributes=audit_attributes(a.attributes),reference=audit_reference(a.reference))
    if a.encoding:result['encoding']=audit_encoding(a.encoding)
    a.out.write_text(json.dumps(result,indent=2));print(' '.join(r['status'] for r in result.values()))
