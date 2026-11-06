"""Import own packed cells and fixed placements, without native P&R."""
import argparse,json,os,shutil,struct,subprocess,sys,time,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from ownpr_image_storage import sha,load_json,copy_original
from capture_runtime_tables import EXPECTED


def worker(out,placement,export=False,sta=False,config_probe=False,experimental_hard_export=False):
    from native_device_session import NativeDeviceSession
    import ctypes as C
    native=NativeDeviceSession(out,full_timing=sta);fn=native.fn;ptr=native.ptr;vp=C.c_void_p
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(native.mem)
    vector=(C.c_uint64*3)();fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vector))
    addresses=[ptr(p) for p in range(vector[0],vector[1],8)]
    io_vector=fn('?getIobufVecReference@Design@NETLIST@@QEAAAEAV?$vector@PEAVIobuf@NETLIST@@V?$allocator@PEAVIobuf@NETLIST@@@std@@@std@@XZ',vp,[vp])(design)
    addresses.extend(ptr(p) for p in range(ptr(io_vector),ptr(io_vector+8),8))
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    def string(buffer):
        a=C.addressof(buffer);n=ptr(a+16);capacity=ptr(a+24)
        if n>4096:raise ValueError('Invalid native name')
        return C.string_at(ptr(a) if capacity>=16 else a,n).decode('utf-8')
    objects={}
    for obj in addresses:
        buffer=C.create_string_buffer(32);getname(obj,C.addressof(buffer));name=string(buffer)
        if name in objects and objects[name]!=obj:raise ValueError('Duplicate native instance')
        objects[name]=obj
    mapping=json.loads((out/'name_map.json').read_text());packed=json.loads((placement/'packed.json').read_text());placed=json.loads((placement/'placement.json').read_text())
    expected=set(mapping['instances'].values());missing=expected-objects.keys();extra=objects.keys()-expected;constant_aliases={}
    cells=packed['modules']['top']['cells'];reverse={v:n for n,v in mapping['instances'].items()}
    for alias in missing:
        kind=cells[reverse[alias]]['type']
        survivors=[a for a in objects if a in reverse and cells[reverse[a]]['type']==kind]
        if kind in ('VCC','GND') and survivors:constant_aliases[alias]=sorted(survivors)[0]
    if extra or missing-set(constant_aliases):
        (out/'instance_difference.json').write_text(json.dumps(dict(missing=sorted(set(mapping['instances'].values())-objects.keys()),extra=sorted(objects.keys()-set(mapping['instances'].values()))),indent=2))
        raise ValueError('Native reader changed the packed instance set')
    print('NATIVE_IMPORT_INSTANCES',len(objects),flush=True)
    setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp]);setlogic=fn('?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp])
    getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])
    bindlut=fn('?setPin2Node@Lut4@NETLIST@@UEAAXXZ',None,[vp]);bindreg=fn('?setPin2Node@Reg@NETLIST@@UEAAXXZ',None,[vp])
    setinit=fn('?setInitValueForRouted@Lut4@NETLIST@@QEAAXV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])
    getinit=fn('?getInitValueForRouted@Lut4@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    bindings=[];hard_bindings=[]
    def bind(name,physical):
        obj=objects[mapping['instances'][name]];setphy(obj,physical);setlogic(physical,obj)
        if getphy(obj)!=physical:raise ValueError('Physical binding not retained')
        return obj
    for group in placed['clusters'].values():
        r,c,slot=group['row']+1,group['column']+1,group['slot'];grid=native.getgrid(native.chip,r,c);aggregate=ptr(grid+0x70)
        lut=ptr(aggregate+8*slot);name=group['lut'];obj=bind(name,lut);cell=packed['modules']['top']['cells'][name];width=int(cell['type'][-1]);init=int(cell['parameters']['INIT'],2)
        word=sum(((init>>(i&((1<<width)-1)))&1)<<i for i in range(16));text=f"16'h{word:04X}".encode();argument=C.create_string_buffer(32)
        C.memmove(argument,text,len(text));C.c_uint64.from_address(C.addressof(argument)+16).value=len(text);C.c_uint64.from_address(C.addressof(argument)+24).value=15
        setinit(obj,C.addressof(argument));C.c_ushort.from_address(lut+0x20).value=word
        check=C.create_string_buffer(32);getinit(obj,C.addressof(check))
        if string(check)!=text.decode():raise ValueError('Native routed LUT INIT mismatch')
        C.c_ubyte.from_address(lut+0x22).value=0;C.c_ubyte.from_address(aggregate+0x326+slot).value=1;bindlut(obj)
        bindings.append(dict(instance=name,native_name=mapping['instances'][name],kind='LUT',site=[r,c,slot],init_word=word))
        if group['ff']:
            reg=ptr(aggregate+0xa0+8*slot);obj=bind(group['ff'],reg);C.c_ubyte.from_address(aggregate+0x318+slot).value=1;bindreg(obj)
            bindings.append(dict(instance=group['ff'],native_name=mapping['instances'][group['ff']],kind='FF',site=[r,c,slot]))
    if placed.get('arithmetic_clusters'):
        from native_ownpr_alu import bind_arithmetic
        arithmetic,carry_audit=bind_arithmetic(native,packed,placed,mapping,bind);bindings.extend(arithmetic)
        (out/'carry_binding_audit.json').write_text(json.dumps(carry_audit,indent=2))
    wide_info=None
    if placed.get('mux_clusters'):
        from native_ownpr_mux import bind_muxes
        mux_bindings,wide_info=bind_muxes(native,packed,placed,mapping,bind);bindings.extend(mux_bindings)
        for group in placed['clusters'].values():
            if group.get('wide_ff_root'):bindreg(objects[mapping['instances'][group['ff']]])
        (out/'wide_mux_binding_audit.json').write_text(json.dumps(wide_info,indent=2))
    getblk=fn('?get_blk@iob@NETLIST@@QEAAPEAVioblk@2@F@Z',vp,[vp,C.c_short]);iologic=fn('?setIobufLogic@iobuf@NETLIST@@QEAAXPEAVIobuf@2@@Z',None,[vp,vp])
    bindio=fn('?setPin2Node@Iobuf@NETLIST@@UEAAXXZ',None,[vp]);getio=fn('?getPhyIobuf@Iobuf@NETLIST@@QEAAPEAViobuf@2@XZ',vp,[vp])
    for name,entry in placed['fixed_io'].items():
        side,index,letter=re.fullmatch(r'IO([TBLR])(\d+)([AB])',entry['physical_site']).groups();index=int(index);r,c={'T':(0,index),'B':(110,index),'L':(index,0),'R':(index,183)}[side]
        block=getblk(native.getgrid(native.chip,r,c),ord(letter)-65);start,end=ptr(block+0x10),ptr(block+0x18)
        if end-start!=8:raise ValueError('Ambiguous physical I/O buffer')
        physical=ptr(start);obj=bind(name,physical);iologic(physical,obj);bindio(obj)
        if getio(obj)!=physical:raise ValueError('I/O binding not retained')
        bindings.append(dict(instance=name,native_name=mapping['instances'][name],kind='IO',site=[r,c,ord(letter)-65],package_pin=entry['package_pin']))
    ioreg_info=None
    if placed.get('ioreg_clusters'):
        from native_ownpr_ioreg import bind_ioregs
        ioreg_bindings,ioreg_info=bind_ioregs(native,packed,placed,mapping,bind);bindings.extend(ioreg_bindings)
        (out/'ioreg_binding_audit.json').write_text(json.dumps(ioreg_info,indent=2))
    if placed.get('hard_clusters'):
        from ownpr_hard_architecture import HardArchitecture
        from ownpr_device import Device
        from native_hard_binding import HardSiteBinding
        hard_audit=HardArchitecture().validate(packed,placed,Device())
        for name,loc in sorted(placed['hard_clusters'].items()):
            obj=objects[mapping['instances'][name]]
            binding=HardSiteBinding(native,obj,'bsram' if loc['family']=='bsram' else 'multalu27x18',loc['row']+1,loc['column']+1)
            hard_bindings.append(binding)
            bindings.append(dict(instance=name,native_name=mapping['instances'][name],kind='HARD',primitive=cells[name]['type'],
                site=[loc['row']+1,loc['column']+1,loc['slot']],cached_logic_matches=ptr(binding.phy+8)==obj))
        (out/'hard_binding_audit.json').write_text(json.dumps(hard_audit,indent=2))
    gsr_names=[name for name,cell in cells.items() if cell['type']=='GSR']
    if len(gsr_names)!=1:raise ValueError('Initial handoff requires one inactive GSR')
    gsr_phy=fn('?getGsr@chip@NETLIST@@QEAAPEAVgsr@2@XZ',vp,[vp])(native.chip)
    if not gsr_phy:raise ValueError('Native global reset resource missing')
    gsr_obj=bind(gsr_names[0],gsr_phy)
    fn('?setPin2Node@Gsr@NETLIST@@UEAAXXZ',None,[vp])(gsr_obj)
    bindings.append(dict(instance=gsr_names[0],native_name=mapping['instances'][gsr_names[0]],kind='GSR',site=list(struct.unpack('<4h',C.string_at(gsr_phy+0x10,8)))))
    print('NATIVE_IMPORTED_BINDINGS',len(bindings),flush=True)
    result=dict(status='placement_imported',logical_instances=len(objects),constant_aliases=constant_aliases,bindings=bindings,vendor_placement_executed=False,vendor_routing_executed=False,
        netlist_semantics_verified=False,io_electrical_applied=False,routes_imported=False,sta_completed=False,bitstream_generated=False)
    result['inverter_lut4_serialization']=True
    (out/'binding_report.json').write_text(json.dumps(result,indent=2))
    net_name=fn('?getName@Net@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    report_name=fn('?getPinNameForRpt@Pin@NETLIST@@QEAAXAEAV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])
    def pin_label(pin):
        buffer=C.create_string_buffer(32);C.c_uint64.from_address(C.addressof(buffer)+24).value=15
        report_name(pin,C.addressof(buffer));return string(buffer).split('.',1)[1]
    nets={};logical_pins=[];connectivity=[]
    for name,cell in cells.items():
        if mapping['instances'][name] in constant_aliases:continue
        cls='Lut4' if cell['type'].startswith('LUT') else 'Mux2' if cell['type'].startswith('MUX2_LUT') else {'DFFRE':'Reg','DFFSE':'Reg','DFFCE':'Reg','DFFPE':'Reg','ALU':'Alu','IBUF':'Iobuf','OBUF':'Iobuf','GSR':'Gsr','VCC':'Vcc','GND':'Gnd','DPX9B':'Bsram','SDPX9B':'Bsram','MULTALU27X18':'NewDsp'}[cell['type']]
        obj=objects[mapping['instances'][name]]
        for direction in ('In','Out'):
            getpins=fn(f'?getAll{direction}Pins@{cls}@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])
            pins=(C.c_uint64*3)();getpins(obj,C.addressof(pins))
            for address in range(pins[0],pins[1],8):
                pin=ptr(address);net=ptr(pin+8)
                if not net:continue
                buffer=C.create_string_buffer(32);net_name(net,C.addressof(buffer));nets[string(buffer)]=net
                logical_pins.append((name,direction,pin,net))
                connectivity.append(dict(instance=name,pin=pin_label(pin),net=string(buffer),direction=direction))
    (out/'native_connectivity.json').write_text(json.dumps(connectivity,indent=2))
    from audit_ownpr_native_export import connectivity_check
    graph_check=connectivity_check(packed,mapping,connectivity,result)
    result['logical_graph_audit']=graph_check;result['netlist_semantics_verified']=True
    if hard_bindings:
        from native_hard_import_audit import capture as capture_hard_import
        hard_readback=capture_hard_import(native,out,packed,placed,mapping,hard_bindings,logical_pins,pin_label)
        result['hard_mode_and_endpoint_readback']=dict(status=hard_readback['status'],hard_cells=hard_readback['hard_cells'],pin_bits=hard_readback['pin_bits'])
    (out/'binding_report.json').write_text(json.dumps(result,indent=2))
    if not export:
        for binding in reversed(hard_bindings):binding.restore()
        result['hard_bindings_restored']=len(hard_bindings)
        (out/'binding_report.json').write_text(json.dumps(result,indent=2));return
    if hard_bindings and not experimental_hard_export:raise ValueError('Hard placement import is verified separately; use explicit experimental capture until complete hard-resource STA/export auditing is enabled')
    if hard_bindings:result['experimental_hard_export']=True;result['hard_resource_signoff_verified']=False
    if sta and (placed.get('arithmetic_clusters') or placed.get('mux_clusters') or placed.get('ioreg_clusters')):
        result['native_import_context']=native.initialize_import_context()
    print('IMPORT_STAGE read_constraints',flush=True)
    if not sta and fn('?readConstraints@NETLIST@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])(native.mem):raise ValueError('Native fixed constraints failed')
    result['constraints_parsed']=True
    # Initial scalar LVCMOS33 bank profile, independently captured from the
    # identical fixed-I/O contract. Never reuse this profile with another CST.
    getbank=fn('?getBank@iobuf@NETLIST@@QEAAFXZ',C.c_short,[vp]);setbank=fn('?setBankVccio@chip@NETLIST@@QEAAXFW4PIO_VALUE@2@@Z',None,[vp,C.c_short,C.c_int])
    used_banks={getbank(getphy(objects[e['native_name']])) for e in bindings if e['kind']=='IO'}
    if (out/'io_reference.json').exists():
        reference=json.loads((out/'io_reference.json').read_text())
        if reference['dll_sha256']!=EXPECTED or reference['cst_sha256']!=sha(out/'project.cst'):raise ValueError('I/O reference contract changed')
        expected={tuple(r['site']) for r in reference['state']['io']}
        actual={tuple(struct.unpack('<4h',C.string_at(getphy(objects[e['native_name']])+0x10,8))) for e in bindings if e['kind']=='IO'}
        if actual!=expected:raise ValueError('Physical I/O sites differ from native calibration')
        bank_values={b:93 for b in used_banks};result['io_bank_calibration_sha256']=reference['state_sha256']
    else:
        bank_source=ROOT/'results/native_sta_mode_002';bank_meta=json.loads((bank_source/'manifest.json').read_text())
        if bank_meta['dll_sha256']!=EXPECTED or bank_meta['cst_sha256']!=sha(out/'project.cst'):raise ValueError('Uncalibrated I/O bank contract')
        if sha(bank_source/'timing_mode_state.json')!='2b1f8bb5424be60544746e6e63561c4802d4095a3ed37f6c73b9cc0e3b034769':raise ValueError('I/O bank calibration changed')
        values=json.loads((bank_source/'timing_mode_state.json').read_text())['bank_vccio']
        if any(b not in range(len(values)) or values[b]!=93 for b in used_banks):raise ValueError('Unsupported initial I/O voltage profile')
        bank_values={b:values[b] for b in used_banks};result['io_bank_calibration_sha256']=sha(bank_source/'timing_mode_state.json')
    for bank,value in bank_values.items():
        setbank(native.chip,bank,value)
        fn('?setBankVccioToArr@chip@NETLIST@@QEAAXFW4PIO_VALUE@2@@Z',None,[vp,C.c_short,C.c_int])(native.chip,bank,value)
    result['io_bank_values']={str(bank):value for bank,value in bank_values.items()}
    set_pio=fn('?setPioAttribute@Iobuf@NETLIST@@QEAAHW4PIO_ATTR@2@W4PIO_VALUE@2@@Z',C.c_int,[vp,C.c_int,C.c_int])
    for entry in bindings:
        if entry['kind']=='IO':
            obj=objects[entry['native_name']];bank=getbank(getphy(obj))
            if set_pio(obj,10,bank_values[bank]):raise ValueError('Logical I/O bank voltage assignment failed')
    expanded=load_json(out/'expanded_routes.json')
    clock=json.loads((out/'clock_tree.json').read_text());parents={};owners={}
    constant_pointers={value:{net for name,direction,pin,net in logical_pins if direction=='Out' and cells[name]['type']==kind}
        for value,kind in ((0,'GND'),(1,'VCC'))}
    constant_records=[];tie_ids=None
    for name,row in expanded['nets'].items():
        rows=clock['parents'] if row['clock'] else row['parents']
        if name.startswith('constant_'):
            from ownpr_constant_routes import owner as constant_owner
            if tie_ids is None:
                from ownpr_device import Device
                d=Device();tie_ids={0:d.ids['VSS'],1:d.ids['VCC']}
            owner,record=constant_owner(name,row,nets,constant_pointers,tie_ids);constant_records.append(record)
        else:
            bit=name.removeprefix('net_');native_name=mapping['nets'].get(bit)
            if native_name not in nets:raise ValueError('Routed net is missing from native design: '+name)
            owner=nets[native_name]
        for entry in rows:
            point=tuple(entry['node']);parent=tuple(entry['parent']) if entry['parent'] else None
            if point in owners and owners[point]!=owner:raise ValueError('Cross-net physical conflict in imported routes')
            parents[point]=parent;owners[point]=owner
    result['constant_routes']=constant_records
    direct_mux_pins=[]
    if wide_info:
        pin_lookup={(name,pin_label(pin)):(pin,net) for name,_,pin,net in logical_pins}
        for link in wide_info['capacity']['links']:
            source_pin,source_net=pin_lookup[link['source'],link['source_pin']];sink_pin,sink_net=pin_lookup[link['destination'],link['pin']]
            if source_net!=sink_net:raise ValueError('Dedicated mux source/sink logical net differs')
            node=ptr(source_pin+0x18)
            if not node:raise ValueError('Dedicated mux source lacks a physical output')
            point=tuple(struct.unpack('<3h',C.string_at(node,6)))
            if point in owners and owners[point]!=source_net:raise ValueError('Dedicated mux output conflicts with another physical net')
            parents[point]=None;owners[point]=source_net;direct_mux_pins.extend((source_pin,sink_pin))
    if ioreg_info:
        pin_lookup={(name,pin_label(pin)):(pin,net) for name,_,pin,net in logical_pins}
        for record in ioreg_info['records']:
            link=record['link'];source_pin,source_net=pin_lookup[link['source'],'Q'];sink_pin,sink_net=pin_lookup[link['destination'],'I']
            if source_net!=sink_net or ptr(source_pin+0x18):raise ValueError('Output I/O direct link logical/source endpoint mismatch')
            node=ptr(sink_pin+0x18)
            if not node:raise ValueError('Output I/O buffer endpoint absent')
            point=tuple(struct.unpack('<3h',C.string_at(node,6)))
            if point in owners and owners[point]!=source_net:raise ValueError('Dedicated I/O output overlaps another net')
            parents[point]=None;owners[point]=source_net;direct_mux_pins.extend((source_pin,sink_pin))
    pointers=native.resolve_routed_nodes(parents)
    for point,node in pointers.items():C.c_uint64.from_address(node+0x20).value=owners[point]
    routed=fn('?setRouted@Net@NETLIST@@QEAAX_N@Z',None,[vp,C.c_bool])
    for net in set(owners.values()):routed(net,True)
    set_pin_routed=fn('?setRouted@Pin@NETLIST@@QEAAX_N@Z',None,[vp,C.c_bool])
    for pin in direct_mux_pins:set_pin_routed(pin,True)
    pin_audit=[]
    for name,direction,pin,net in logical_pins:
        node=ptr(pin+0x18)
        if not node or net not in owners.values():continue
        point=struct.unpack('<3h',C.string_at(node,6));match=point in pointers and pointers[point]==node and owners[point]==net
        pin_audit.append(dict(instance=name,direction=direction,node=point,match=match))
        if match:set_pin_routed(pin,True)
    (out/'pin_route_audit.json').write_text(json.dumps(pin_audit,indent=2))
    result.update(routes_imported=True,physical_route_nodes=len(parents),routed_nets=len(set(owners.values())))
    (out/'binding_report.json').write_text(json.dumps(result,indent=2))
    geometry={name:C.string_at(getphy(objects[alias])+0x10,8).hex() for name,alias in mapping['instances'].items() if alias in objects and getphy(objects[alias])}
    with native.resolved_parent_tree(parents,pointers):
        before={p:ptr(n+0x18) for p,n in pointers.items()}
        if sta:
            endpoint_query=fn('?getNodebyPinForSta@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z',C.c_bool,[vp,vp,vp])
            direct=fn('?isDirectConn@Pin@NETLIST@@QEAA_NPEAVglobalMem@@@Z',C.c_bool,[vp,vp])
            captures=[]
            for name,direction,pin,net in logical_pins:
                if net not in owners.values():continue
                label=pin_label(pin)
                vec=(C.c_uint64*3)();endpoint_query(pin,C.addressof(vec),native.mem);nodes=[]
                for a in range(vec[0],vec[1],8):
                    node=ptr(a);chain=[];visited=set();current=node
                    while current and current not in visited:
                        visited.add(current);chain.append(struct.unpack('<3h',C.string_at(current,6)));current=ptr(current+0x18)
                    values=[[sum(native.delay_pointer(p,s)[i] for p in visited) for i in range(4)] for s in range(3)]
                    nodes.append(dict(node=struct.unpack('<3h',C.string_at(node,6)),chain=chain,wire_delays=values))
                captures.append(dict(instance=name,native_name=mapping['instances'][name],pin=label,direction=direction,nodes=nodes,direct_connection=bool(direct(pin,native.mem)),flags=C.c_ubyte.from_address(pin+0x4f).value))
            (out/'sta_endpoint_audit.json').write_text(json.dumps(captures,indent=2))
        if sta:
            print('IMPORT_STAGE routed_sta',flush=True)
            # Final-report mode captured from the native completed flow in
            # native_sta_mode_001. This selects routed rather than estimate STA.
            fn('?setFlowFlag@TmMonitor@STA@@QEAAXW4FLOW_FLAG_STA@2@@Z',None,[vp,C.c_int])(native.monitor,7)
            # routeUsed is an optimization-phase estimate selector, not the
            # final routed-report mode (12e9594..12e9c83). Match final native
            # mode: flow=7, placeUsed=routeUsed=0, then rebuild/evaluate STA.
            fn('?setPlaceFlag@TmMonitor@STA@@QEAAX_N@Z',None,[vp,C.c_bool])(native.monitor,False)
            C.c_ubyte.from_address(native.monitor+0x12).value=0
            fn('?restartTiming@TmMonitor@STA@@QEAAXXZ',None,[vp])(native.monitor)
            C.CFUNCTYPE(C.c_bool,vp)(native.dll._handle+0x13b77b0)(native.monitor)
            print('IMPORT_STAGE timing_report',flush=True)
            result['sta_report_returncode']=fn('?generateTimingRptoutFile@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])(native.mem)
            result['sta_report_generated']=(out/'probe.tr').exists()
            if placed.get('arithmetic_clusters'):
                from native_extended_timing import capture as capture_extended
                capture_extended(native,out)
            if placed.get('mux_clusters'):
                from native_mux_timing import capture as capture_mux
                capture_mux(native,out)
            if ioreg_info:
                from native_ioreg_timing import capture as capture_ioreg_timing
                capture_ioreg_timing(native,out)
            if hard_bindings:
                from native_hard_timing import capture_all as capture_hard_timing
                capture_hard_timing(native,out,vendor_pnr_executed=False)
            (out/'binding_report.json').write_text(json.dumps(result,indent=2))
        print('IMPORT_STAGE native_encoder',len(parents),'nodes',flush=True)
        from native_io_state import capture
        (out/'io_state_before_encoding.json').write_text(json.dumps(capture(native),indent=2))
        if fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])(native.mem):raise ValueError('Native serialization failed')
        # Match the native production flow: one encoding in a fresh process.
        # Re-encoding mutates unused-pad default bits, so a stable repeated
        # encoding is a calibration profile, not a production correctness gate.
        result['encoder_profile']='fresh_process_single_pass'
        (out/'io_state_after_encoding.json').write_text(json.dumps(capture(native),indent=2))
        if ioreg_info:
            from native_ioreg_state import capture as capture_ioreg_state
            (out/'ioreg_state.json').write_text(json.dumps(capture_ioreg_state(native),indent=2))
        if config_probe:
            from native_io_encoding_probe import probe
            production={suffix:(out/('probe'+suffix)).read_bytes() for suffix in ('.fs','.bin','.binx')}
            try:probe(native,out,objects,mapping)
            finally:
                for suffix,data in production.items():(out/('probe'+suffix)).write_bytes(data)
        fn('?generateTxtPinoutFile@@YAXPEAVglobalMem@@@Z',None,[vp])(native.mem)
        result['cst_export_returncode']=fn('?generateCstoutFile@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])(native.mem)
        result['io_bank_after_encoding']={str(bank):fn('?getBankVccio@chip@NETLIST@@QEAA?AW4PIO_VALUE@2@F@Z',C.c_int,[vp,C.c_short])(native.chip,bank) for bank in used_banks}
        after={p:ptr(n+0x18) for p,n in pointers.items()}
        geometry_after={name:C.string_at(getphy(objects[alias])+0x10,8).hex() for name,alias in mapping['instances'].items() if alias in objects and getphy(objects[alias])}
        result.update(bitstream_generated=(out/'probe.fs').exists(),encoding_preserved_parents=before==after,encoding_preserved_geometry=geometry==geometry_after,
            full_bitstream_verified=False,sta_completed=False)
    (out/'binding_report.json').write_text(json.dumps(result,indent=2))
    print('IMPORT_ENCODER_RESULT',result['bitstream_generated'],result['encoding_preserved_parents'],result['encoding_preserved_geometry'],flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--placement',type=Path,default=ROOT/'results/ownpr_int2float_place_004');ap.add_argument('--worker',action='store_true');ap.add_argument('--export',action='store_true');ap.add_argument('--sta',action='store_true')
    ap.add_argument('--routes',type=Path,default=ROOT/'results/ownpr_int2float_route_008');ap.add_argument('--wire-timing',type=Path,default=ROOT/'results/ownpr_int2float_wire_timing_007')
    ap.add_argument('--partial',type=Path,default=ROOT/'results/ownpr_clock_partial_001/partial_configuration.json')
    ap.add_argument('--config-probe',action='store_true')
    ap.add_argument('--experimental-hard-export',action='store_true',help='Capture native STA/encoding for audited hard placement; final hard-resource signoff is still required')
    ap.add_argument('--cst',type=Path,default=ROOT/'results/ownpr_clock_tree_001/clock_auto.cst')
    ap.add_argument('--sdc',type=Path,default=ROOT/'results/fullio_closure_003/int_lut4_local155_edge.sdc')
    ap.add_argument('--io-reference',type=Path)
    ap.add_argument('--clock-tree',type=Path,default=ROOT/'results/ownpr_clock_tree_001/clock_tree.json')
    a=ap.parse_args();out=a.out.resolve();placement=a.placement.resolve()
    if a.sta and not a.export:ap.error('--sta requires --export')
    if a.config_probe and not a.export:ap.error('--config-probe requires --export')
    if a.experimental_hard_export and not a.export:ap.error('--experimental-hard-export requires --export')
    if a.worker:return worker(out,placement,a.export,a.sta,a.config_probe,a.experimental_hard_export)
    route_meta=json.loads((a.routes/'manifest.json').read_text())
    if a.export and route_meta.get('status')!='routed':raise ValueError('Native export requires completed own routing')
    if sha(placement/'packed.json')!=route_meta['netlist_sha256'] or sha(placement/'placement.json')!=route_meta['placement_sha256']:
        raise ValueError('This initial adapter requires the placement used by the frozen data/clock routes')
    from ownpr_native_netlist import emit
    from workspace_budget import reserve
    reserve(ROOT,600_000_000)
    hard=None
    if a.experimental_hard_export:
        from ownpr_hard_architecture import HardArchitecture
        hard=HardArchitecture()
    packed_design=json.loads((placement/'packed.json').read_text())
    prepare(out);text,mapping=emit(packed_design,hard=hard);(out/'probe.vg').write_text(text);(out/'name_map.json').write_text(json.dumps(mapping,indent=2))
    wire=load_json(a.wire_timing/'wire_delays.json');wire_meta=json.loads((a.wire_timing/'manifest.json').read_text())
    if (wire['source_routes_sha256']!=sha(a.routes/'routes.json') or wire['source_nets_sha256']!=sha(a.routes/'nets.json')
            or wire_meta['expanded_routes_sha256']!=sha(a.wire_timing/'expanded_routes.json')):raise ValueError('Expanded routes do not match selected own routing')
    copy_original(a.wire_timing/'expanded_routes.json',out/'expanded_routes.json')
    copy_original(a.partial,out/'partial_configuration.json')
    shutil.copyfile(a.cst,out/'project.cst');shutil.copyfile(a.sdc,out/'probe.sdc');shutil.copyfile(a.clock_tree,out/'clock_tree.json')
    # A separate dedicated clock is absent from the ordinary-route artifact.
    if (a.routes/'all_nets.json').exists():
        from ownpr_clock_partition import partition
        from ownpr_device import Device,RoutingGraph
        all_nets=json.loads((a.routes/'all_nets.json').read_text());clock=json.loads(a.clock_tree.read_text())
        ordinary,clock_info=partition(RoutingGraph(Device(),ROOT/'results/native_rrg_001'),all_nets,clock)
        if ordinary!=json.loads((a.routes/'nets.json').read_text()):raise ValueError('Clock partition differs from routed net set')
        expanded=json.loads((out/'expanded_routes.json').read_text())
        if clock_info['net'] in expanded['nets']:raise ValueError('Dedicated clock duplicates ordinary expanded net')
        expanded['nets'][clock_info['net']]=dict(clock=True,parents=clock['parents'])
        (out/'expanded_routes.json').write_text(json.dumps(expanded,indent=2))
    if a.io_reference:
        from ownpr_io_reference import load
        reference=load(a.io_reference,a.cst);(out/'io_reference.json').write_text(json.dumps(reference,indent=2))
    if a.sta:
        # Reporting directives only: preserve every timing constraint verbatim.
        report_limit=max(1000,sum(len(bs) for c in packed_design['modules']['top']['cells'].values() for pin,bs in c['connections'].items() if c['port_directions'][pin]=='input')) if a.experimental_hard_export else 1000
        sdc=out/'probe.sdc';sdc.write_text(sdc.read_text()+''.join(f'\nreport_timing -{kind} -max_paths {report_limit} -max_common_paths 1' for kind in ('setup','hold','recovery','removal'))+f'\nreport_min_pulse_width -nworst {report_limit} -detail\n')
    cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+f'-cst {(out/"project.cst").as_posix()}\n-bit\n');shutil.copyfile(__file__,out/'adapter_source.py.txt')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;expired=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out),'--placement',str(placement)]+(['--export'] if a.export else [])+(['--sta'] if a.sta else [])+(['--config-probe'] if a.config_probe else [])+(['--experimental-hard-export'] if a.experimental_hard_export else []),cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=600 if a.config_probe else 360 if a.experimental_hard_export else 120).returncode
        except subprocess.TimeoutExpired:expired=True
    result=dict(status='imported' if rc==0 and (out/'binding_report.json').exists() else 'failed',returncode=rc,timeout=expired,wall_seconds=time.monotonic()-start,
        dll_sha256=EXPECTED,packed_sha256=sha(placement/'packed.json'),placement_sha256=sha(placement/'placement.json'),exported_netlist_sha256=sha(out/'probe.vg'),
        vendor_placement_executed=False,vendor_routing_executed=False,sta_completed=False,bitstream_generated=False)
    if (out/'binding_report.json').exists():
        bound=json.loads((out/'binding_report.json').read_text());result['bitstream_generated']=bound.get('bitstream_generated',False)
    result['sta_report_generated']=bool(bound.get('sta_report_generated',False)) if (out/'binding_report.json').exists() else False
    result['source_sdc_sha256']=sha(a.sdc)
    result['clock_tree_sha256']=sha(out/'clock_tree.json')
    result['sdc_sha256']=sha(out/'probe.sdc');result['cst_sha256']=sha(out/'project.cst')
    result['adapter_sha256']=sha(out/'adapter_source.py.txt')
    result['helper_sha256']={name:sha(ROOT/'scripts'/name) for name in ('ownpr_native_netlist.py','native_ownpr_alu.py','ownpr_io_reference.py','audit_ownpr_native_export.py','native_device_session.py','native_extended_timing.py','ownpr_extended_timing_model.py','audit_ownpr_supported_timing.py','audit_native_io_configuration.py','native_ownpr_mux.py','native_mux_timing.py','ownpr_mux_architecture.py','ownpr_mux_macros.py','ownpr_mux_configuration.py')}
    result['helper_sha256']['ownpr_carry_lut_sharing.py']=sha(ROOT/'scripts/ownpr_carry_lut_sharing.py')
    result['helper_sha256']['ownpr_effective_io_constraints.py']=sha(ROOT/'scripts/ownpr_effective_io_constraints.py')
    result['helper_sha256']['ownpr_image_storage.py']=sha(ROOT/'scripts/ownpr_image_storage.py')
    if json.loads((placement/'placement.json').read_text()).get('shared_ff_calibration'):
        for name in ('ownpr_shared_ff_packing.py','ownpr_ff_configuration.py','ownpr_shared_ff_calibration.py','audit_shared_ff_route.py'):
            result['helper_sha256'][name]=sha(ROOT/'scripts'/name)
    if any(r.get('wide_ff_root') for r in json.loads((placement/'placement.json').read_text())['clusters'].values()):
        for name in ('ownpr_wide_ff_packing.py','ownpr_wide_ff_configuration.py','audit_ownpr_wide_ff.py'):
            result['helper_sha256'][name]=sha(ROOT/'scripts'/name)
    result['experimental_hard_export']=a.experimental_hard_export
    if a.experimental_hard_export:
        for name in ('ownpr_hard_architecture.py','native_hard_binding.py','native_hard_import_audit.py','native_hard_timing.py','ownpr_hard_sta.py','ownpr_timing_endpoints.py','audit_ownpr_extended_sta.py'):result['helper_sha256'][name]=sha(ROOT/'scripts'/name)
        if (out/'hard_import_readback.json').exists():result['hard_import_readback_sha256']=sha(out/'hard_import_readback.json')
        if (out/'hard_timing_index.json').exists():result['hard_timing_index_sha256']=sha(out/'hard_timing_index.json')
    if a.io_reference:result['io_reference_sha256']=sha(out/'io_reference.json')
    result['routes_sha256']=sha(a.routes/'routes.json');result['expanded_routes_sha256']=sha(out/'expanded_routes.json')
    result['partial_configuration_sha256']=sha(a.partial)
    if (out/'extended_timing_queries.json').exists():result['extended_timing_queries_sha256']=sha(out/'extended_timing_queries.json')
    if (out/'mux_timing_queries.json').exists():result['mux_timing_queries_sha256']=sha(out/'mux_timing_queries.json')
    for file in ('ioreg_state.json','ioreg_timing_queries.json'):
        if (out/file).exists():result[file.replace('.json','_sha256')]=sha(out/file)
    for name in ('native_ownpr_ioreg.py','native_ioreg_state.py','native_ioreg_timing.py','ownpr_ioreg_macros.py','ownpr_ioreg_configuration.py','ownpr_ioreg_timing.py','audit_ioreg_preset_timing.py','audit_ownpr_ioreg.py','ownpr_constant_routes.py','ownpr_timing_numbers.py'):
        result['helper_sha256'][name]=sha(ROOT/'scripts'/name)
    (out/'manifest.json').write_text(json.dumps(result,indent=2))
    if a.sta and result['status']=='imported':
        from audit_ownpr_native_export import audit
        try:
            checked=audit(out,placement,out/'partial_configuration.json');(out/'handoff_audit.json').write_text(json.dumps(checked,indent=2))
            result.update(sta_completed=True,handoff_audit_sha256=sha(out/'handoff_audit.json'),timing_signoff=False,full_bitstream_verified=False)
            bound.update(sta_completed=True,packed_graph_import_verified=True,io_electrical_report_verified=True,timing_signoff=False)
            (out/'binding_report.json').write_text(json.dumps(bound,indent=2))
        except Exception as exc:
            result.update(status='handoff_audit_failed',audit_error=str(exc))
        (out/'manifest.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    if result['status']!='imported':raise SystemExit(1)


if __name__=='__main__':main()
