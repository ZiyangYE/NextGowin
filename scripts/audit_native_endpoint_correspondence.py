"""Audit captured logical pins against parent trees; unresolved static cases stay open."""
import argparse,collections,json,struct
from pathlib import Path
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
ROOT=Path(__file__).resolve().parents[1]

def audit(snapshot,bindings=None,cls_evidence=None,ioreg_evidence=None,gsr_evidence=None):
    pins=snapshot.get('logical_pin_nodes',{});nodes=snapshot['routed_endpoint_parent_nodes'];nets=snapshot['logical_net_connections']
    if not pins:raise ValueError('Missing logical-pin-to-physical-node capture')
    unresolved=[];errors=[];ownership=collections.defaultdict(set);matched=0;empty=0;endpoints=0;logical_sinks=0;owners={};static=[]
    from ownpr_mux_architecture import children
    cls_groups={}
    io_rows={}
    if ioreg_evidence:
        if ioreg_evidence['status']!='output_ioreg_bindings_controls_and_encoded_bits_verified':raise ValueError('Unverified output-register contract')
        io_rows={r['instance']:r for r in ioreg_evidence['registers']}
        if len(io_rows)!=len(ioreg_evidence['registers']):raise ValueError('Ambiguous output-register identities')
    io_sites={r['instance']:r['site'] for r in snapshot.get('io',{}).get('io',[])}
    if cls_evidence:
        if cls_evidence['status']!='captured_cls_attributes_match_encoded_bits' or cls_evidence['mismatches']:raise ValueError('CLS bits have not matched')
        cls_groups={tuple(r['site']):r['attributes'] for r in cls_evidence['groups']}
    by_site={}
    for row in bindings or []:
        header=row.get('physical_header')
        if header is not None:
            key=(row['name'],tuple(header))
            if key in by_site:raise ValueError('Ambiguous native physical binding')
            by_site[key]=row
    def binding(pin):
        g=snapshot['geometry'].get(str(pin['owner']),{});raw=g.get('physical_header')
        return by_site.get((g.get('name'),tuple(struct.unpack('<4h',bytes.fromhex(raw))))) if raw else None
    def dedicated(source,sink):
        a,b=binding(source),binding(sink)
        if not a or not b or sink.get('inverted'):return None
        ah,bh=a['physical_header'],b['physical_header'];sp=source['label'].rsplit('.',1)[-1];dp=sink['label'].rsplit('.',1)[-1]
        if source['logical_class']=='.?AVLut4@NETLIST@@' and sink['logical_class']=='.?AVReg@NETLIST@@' and sp=='F' and dp=='D':
            companion=b.get('ff_companion_lut',{})
            if ah[0]==24 and bh[0]==50 and ah[1:]==bh[1:] and companion.get('header')==ah and companion.get('input_mode')==0 and companion.get('init_word')==a.get('physical_init_word'):
                return dict(kind='same_slot_lut_to_ff',source_site=ah,destination_site=bh)
        if sink['logical_class']=='.?AVMux2@NETLIST@@' and dp in ('I0','I1') and b.get('physical_mux_cached_logic_matches') is True:
            try:typ,slot=children(bh[0]-28,bh[3])[int(dp[1])]
            except ValueError:return None
            expected=[24 if typ=='LUT4' else int(typ[-1])+28,bh[1],bh[2],slot]
            expected_class='.?AVLut4@NETLIST@@' if typ=='LUT4' else '.?AVMux2@NETLIST@@'
            if ah==expected and source['logical_class']==expected_class and sp==('F' if typ=='LUT4' else 'O'):
                return dict(kind='native_dedicated_mux_child',source_site=ah,destination_site=bh,pin=dp)
        return None
    def point(n):return list(struct.unpack('<3h',bytes.fromhex(nodes[str(n)][0]))) if str(n) in nodes else None
    for net,(driver,sinks) in nets.items():
        if not sinks:empty+=1;continue
        if not driver or str(driver) not in pins:errors.append(dict(net=net,reason='missing_driver'));continue
        source=pins[str(driver)];sources=set(source['nodes']);owners[driver]=source['label'];logical_sinks+=len(sinks)
        for sink in sinks:
            if str(sink) not in pins:errors.append(dict(net=net,sink=sink,reason='missing_sink_capture'));continue
            row=pins[str(sink)]
            if row['net']!=int(net):errors.append(dict(net=net,sink=row['label'],reason='logical_net_identity_mismatch'));continue
            endpoints+=len(row['nodes'])
            if gsr_evidence and gsr_evidence.get('status')=='constant_inactive_gsr_and_encoded_binding_verified' and row['logical_class']=='.?AVGsr@NETLIST@@' and source['logical_class']=='.?AVVcc@NETLIST@@' and not row.get('inverted') and row['label']==gsr_evidence['sink'] and source['label']==gsr_evidence['driver']:
                static.append(dict(net=net,driver=source['label'],sink=row['label'],kind='encoded_constant_inactive_global_reset'));continue
            source_name,_,source_pin=source['label'].rpartition('.');sink_name,_,sink_pin=row['label'].rpartition('.')
            output=io_rows.get(source_name)
            if output and source_pin=='Q' and sink_pin=='I' and source['logical_class']=='.?AVReg@NETLIST@@' and row['logical_class']=='.?AVIobuf@NETLIST@@':
                g=snapshot['geometry'].get(str(source['owner']),{});header=list(struct.unpack('<4h',bytes.fromhex(g['physical_header']))) if g.get('physical_header') else None
                if sink_name==output['iobuf'] and io_sites.get(sink_name)==[0,*output['site']] and header==[0,output['site'][0],output['site'][1],1]:
                    static.append(dict(net=net,driver=source['label'],sink=row['label'],kind='encoded_output_register_direct_link',site=output['site']));continue
            output=io_rows.get(sink_name)
            if output and sink_pin in output['constant_control_pins'] and source['logical_class'] in ('.?AVVcc@NETLIST@@','.?AVGnd@NETLIST@@') and row['logical_class']=='.?AVReg@NETLIST@@':
                g=snapshot['geometry'].get(str(row['owner']),{});header=list(struct.unpack('<4h',bytes.fromhex(g['physical_header']))) if g.get('physical_header') else None
                value=int(source['logical_class']=='.?AVVcc@NETLIST@@')^int(bool(row.get('inverted')));expected=output['semantics']['ce' if sink_pin=='CE' else 'control']
                if header==[0,output['site'][0],output['site'][1],1] and expected==('one' if value else 'zero'):
                    static.append(dict(net=net,driver=source['label'],sink=row['label'],kind='encoded_output_register_constant_control',site=output['site'],value=value));continue
            control=row['label'].rsplit('.',1)[-1]
            if source['logical_class'] in ('.?AVVcc@NETLIST@@','.?AVGnd@NETLIST@@') and row['logical_class']=='.?AVReg@NETLIST@@' and control in ('CE','CLEAR','PRESET','RESET','SET') and not row.get('inverted'):
                reg=binding(row)
                if reg:
                    header=reg['physical_header'];kind,r,c,slot=header
                    attrs=cls_groups.get((r-1,c-1,slot//2))
                    cached=next((g['attributes'] for g in reg.get('cfu_cls_attributes',[]) if g['group']==slot//2),None)
                    if attrs is not None and attrs!=cached:raise ValueError('CLS certificate differs from native register attributes')
                    ce=[p['node'] for p in reg.get('register_ports',[]) if (p.get('name') or '').startswith('CE' if control=='CE' else 'LSR')]
                    actual=[point(n) for n in row['nodes']]
                    value=int(source['logical_class']=='.?AVVcc@NETLIST@@');offset=5 if control=='CE' else 9
                    if kind==50 and attrs and (attrs[0] or attrs[1]) and attrs[offset:offset+2]==[1+value,0] and len(actual)==1 and actual==ce:
                        static.append(dict(net=net,driver=source['label'],sink=row['label'],kind='encoded_constant_'+('enable' if control=='CE' else 'control')+('_one' if value else '_zero'),group=[r-1,c-1,slot//2],physical_endpoints=len(actual)));continue
            if not row['nodes']:
                proof=dedicated(source,row)
                if proof:
                    static.append(dict(net=net,driver=source['label'],sink=row['label'],**proof));continue
                unresolved.append(dict(net=net,driver=source['label'],sink=row['label'],reason='sink_without_physical_nodes',source_class=source['logical_class'],sink_class=row['logical_class']));continue
            for endpoint in row['nodes']:
                seen=set();path=[];node=endpoint
                while node and node not in sources:
                    if node in seen:errors.append(dict(net=net,sink=row['label'],reason='parent_cycle'));break
                    if str(node) not in nodes:errors.append(dict(net=net,sink=row['label'],reason='missing_parent_capture'));break
                    seen.add(node);path.append(node);node=nodes[str(node)][1]
                if node in sources:
                    path.append(node);matched+=1
                    for resource in path:ownership[resource].add(driver)
                else:unresolved.append(dict(net=net,driver=source['label'],sink=row['label'],reason='driver_not_reached',source_nodes=[point(n) for n in sources],root=point(path[-1]) if path else None,source_class=source['logical_class'],sink_class=row['logical_class']))
    conflicts=[dict(node=n,point=point(n),drivers=[owners[v] for v in sorted(vs)]) for n,vs in ownership.items() if len(vs)>1]
    return dict(status='all_captured_routed_endpoints_correspond' if not(unresolved or errors or conflicts) else 'partial_endpoint_correspondence',logical_nets=len(nets),empty_nets=empty,logical_sinks=logical_sinks,physical_endpoints=endpoints,matched_physical_endpoints=matched,
        shared_path_resources_checked=len(ownership),cross_driver_conflicts=conflicts,errors=errors,static_connections=static,static_connection_counts=dict(collections.Counter(r['kind'] for r in static)),unresolved=unresolved,unresolved_reasons=dict(collections.Counter(r['reason'] for r in unresolved)),
        scope='Exact native node-pointer ancestry to declared logical drivers. Static/dedicated/constant/alias exceptions require separate explicit rules. No bitstream PIP validation or complete baseline acceptance implied.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('snapshot',type=Path);p.add_argument('--bindings',type=Path);p.add_argument('--cls-audit',type=Path);p.add_argument('--ioreg-audit',type=Path);p.add_argument('--gsr-audit',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args();reserve(ROOT,2000000)
    cls=load_json(a.cls_audit) if a.cls_audit else None
    if cls and (not a.bindings or cls['bindings_sha256']!=sha(a.bindings) or cls['image_sha256']!=sha(a.bindings.parent/'probe.fs')):raise ValueError('CLS evidence provenance differs')
    io=load_json(a.ioreg_audit) if a.ioreg_audit else None
    gsr=load_json(a.gsr_audit) if a.gsr_audit else None
    for evidence in (io,gsr):
        if not evidence:continue
        hashes={Path(k).resolve():v for k,v in evidence['source_sha256'].items()}
        if hashes.get(a.snapshot.resolve())!=sha(a.snapshot):raise ValueError('I/O register contract uses another snapshot')
        for p,h in hashes.items():
            if sha(p)!=h:raise ValueError('I/O register evidence changed')
    result=audit(load_json(a.snapshot),load_json(a.bindings) if a.bindings else None,cls,io,gsr);result['source_sha256']=sha(a.snapshot)
    if a.gsr_audit:result['gsr_audit_sha256']=sha(a.gsr_audit)
    if a.ioreg_audit:result['ioreg_audit_sha256']=sha(a.ioreg_audit)
    if a.cls_audit:result['cls_audit_sha256']=sha(a.cls_audit)
    if a.bindings:result['bindings_sha256']=sha(a.bindings)
    with a.out.open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ('unresolved','errors','cross_driver_conflicts','static_connections')},indent=2))
