"""Fixed-pin vendor calibration of primitive binding, not own P&R.

Reads the native in-memory result in an isolated process. No open device
database, SDK modification, pin reassignment, or device programming.
"""
import argparse, gzip, json, os, re, shutil, struct, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from gowin_probe import BIN, prepare
from capture_runtime_tables import EXPECTED
from run_abc_reproduction import sha


def worker(out, encode_probe=False, physical_init=False, init_sweep=False, capture_routes=False, capture_wire_delays=False, sta_endpoints=False):
    handles = [os.add_dll_directory(str(p)) for p in
               (Path(sys.base_prefix)/'DLLs', Path(sys.base_prefix)/'Library/bin', BIN)]
    import ctypes as C
    C.windll.kernel32.SetErrorMode(0x8003)
    if sha(BIN/'gowin.dll') != EXPECTED:
        raise ValueError('Unreviewed native ABI')
    os.chdir(out)
    dll = C.CDLL(str(BIN/'gowin.dll'))
    vp = C.c_void_p
    def fn(name, result, args):
        f = getattr(dll, name); f.restype = result; f.argtypes = args
        return f
    parse = fn('?readInUserCommand@@YAHHPEAPEAD_N@Z', C.c_int,
               [C.c_int, C.POINTER(C.c_char_p), C.c_bool])
    argv = [str(BIN/'gowin.exe'), '-do', str(out/'cmd.do')]
    av = (C.c_char_p*len(argv))(*(x.encode() for x in argv))
    if parse(len(argv), av, True): raise ValueError('Argument parsing failed')
    rc = fn('?executePnrTask@@YAHXZ', C.c_int, [])()
    print('CALIBRATION_RETURN', rc, flush=True)
    if rc: raise ValueError('Native calibration failed')
    mem = fn('?getGlobalMemPtr@@YAPEAVglobalMem@@XZ', vp, [])()
    design = fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ', vp, [vp])(mem)
    if not design: raise ValueError('Native design not retained')
    vector = (C.c_uint64*3)()
    fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',
       None, [vp,vp])(design, C.addressof(vector))
    start, end, capacity = vector
    if not start or not start <= end <= capacity or (end-start)%8 or (end-start)//8 > 1000000:
        raise ValueError('Invalid native instance vector')
    getname = fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ', vp,[vp,vp])
    getphy = fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])
    getreg = fn('?getPhyReg@Reg@NETLIST@@QEAAPEAVreg@2@XZ',vp,[vp])
    getpin = fn('?getOnePin@Reg@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint])
    pinnodes = fn('?getNodebyPin@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z',C.c_bool,[vp,vp,vp])
    if sta_endpoints:
        pinnodes = fn('?getNodebyPinForSta@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z',C.c_bool,[vp,vp,vp])
    def classname(obj):
        if not obj: return None
        vt = C.c_uint64.from_address(obj).value
        if not dll._handle <= vt < dll._handle+0x2000000: return None
        loc = C.c_uint64.from_address(vt-8).value
        td = C.c_uint32.from_address(loc+12).value
        return C.string_at(dll._handle+td+16).decode('ascii')
    def string(p):
        size = C.c_uint64.from_address(p+16).value
        cap = C.c_uint64.from_address(p+24).value
        if size > 4096: raise ValueError('Invalid native string')
        return C.string_at(C.c_uint64.from_address(p).value if cap>=16 else p,size).decode(errors='replace')
    table=fn('?getRouteNodeTable@globalMem@@QEAAPEAVrouteNodeTable@TEMPLATE@@XZ',vp,[vp])(mem)
    chip=fn('?getDevChip@globalMem@@QEAAPEAVchip@NETLIST@@XZ',vp,[vp])(mem)
    if (out/'hard_resources_requested.json').exists():
        from native_hard_resource_capture import capture as capture_hard
        capture_hard(out,fn,mem,vector,getname,getphy,classname,string,pinnodes)
    if (out/'clock_chains_requested.json').exists():
        from native_clock_tree_capture import capture as capture_clock_chains
        capture_clock_chains(out,fn,mem,vector,getname,classname,string,pinnodes)
    from types import SimpleNamespace
    if (out/'hard_timing_requested.json').exists():
        from native_hard_timing import capture_all
        capture_all(SimpleNamespace(fn=fn,ptr=lambda p:C.c_uint64.from_address(p).value,mem=mem,chip=chip,dll=dll),out)
    if (out/'ioreg_capture_requested.json').exists():
        from native_ioreg_state import capture as capture_ioreg
        (out/'ioreg_state.json').write_text(json.dumps(capture_ioreg(SimpleNamespace(fn=fn,ptr=lambda p:C.c_uint64.from_address(p).value,mem=mem,chip=chip,dll=dll)),indent=2))
    if (out/'mux_timing_requested.json').exists():
        from native_mux_timing import capture as capture_mux_timing
        capture_mux_timing(SimpleNamespace(fn=fn,ptr=lambda p:C.c_uint64.from_address(p).value,mem=mem,chip=chip,dll=dll),out)
    if (out/'extended_timing_requested.json').exists():
        from native_extended_timing import capture as capture_extended
        capture_extended(SimpleNamespace(fn=fn,ptr=lambda p:C.c_uint64.from_address(p).value,mem=mem,chip=chip,dll=dll),out)
    from native_io_state import capture
    (out/'io_configuration_state.json').write_text(json.dumps(capture(SimpleNamespace(fn=fn,ptr=lambda p:C.c_uint64.from_address(p).value,mem=mem,chip=chip)),indent=2))
    getgrid=fn('?getGridByRowCol@chip@NETLIST@@QEAAPEAVgrid@2@FF@Z',vp,[vp,C.c_short,C.c_short])
    gettype=fn('?getType@Instance@NETLIST@@QEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp])
    count=C.c_ushort.from_address(table+0x341a).value
    names={i:string(table+0x18+32*i) for i in range(min(count,320))}
    rows=[];lut_objects=[];known_pins={}
    lutpin=fn('?getOnePin@Lut4@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint])
    for i in range((end-start)//8):
        obj=C.c_uint64.from_address(start+8*i).value
        typ=classname(obj)
        if typ not in ('.?AVReg@NETLIST@@','.?AVLut4@NETLIST@@','.?AVAlu@NETLIST@@','.?AVMux2@NETLIST@@'): continue
        buf=C.create_string_buffer(32); getname(obj,C.addressof(buf))
        phy=getphy(obj)
        row=dict(name=string(C.addressof(buf)),logical_class=typ,physical_class=classname(phy))
        row['logical_type_enum']=gettype(obj)
        if phy: row['physical_header']=list(struct.unpack('<4H',C.string_at(phy+0x10,8)))
        if typ=='.?AVMux2@NETLIST@@':
            row['mux_subtype']=fn('?getSubType@Mux2@NETLIST@@UEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp])(obj)
            initbuf=C.create_string_buffer(32);fn('?getInitValueForRouted@Mux2@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])(obj,C.addressof(initbuf));row['routed_init']=string(C.addressof(initbuf))
            row['pins']=[];pinlabel=fn('?getPinNameForRpt@Pin@NETLIST@@QEAAXAEAV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])
            for direction in ('In','Out'):
                pins=(C.c_uint64*3)();fn(f'?getAll{direction}Pins@Mux2@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(obj,C.addressof(pins))
                for address in range(pins[0],pins[1],8):
                    pin=C.c_uint64.from_address(address).value;label=C.create_string_buffer(32);C.c_uint64.from_address(C.addressof(label)+24).value=15;pinlabel(pin,C.addressof(label));pin_name=string(C.addressof(label)).split('.',1)[1]
                    known_pins[pin]=dict(instance=row['name'],kind='MUX',pin_name=pin_name)
                    node=C.c_uint64.from_address(pin+0x18).value;net=C.c_uint64.from_address(pin+8).value;source=C.c_uint64.from_address(net+0x18).value if net else 0
                    source_obj=C.c_uint64.from_address(source+0x10).value if source else 0;source_phy=getphy(source_obj) if source_obj else 0;source_buf=C.create_string_buffer(32)
                    if source_obj:getname(source_obj,C.addressof(source_buf))
                    row['pins'].append(dict(pin=pin_name,direction=direction,node=list(struct.unpack('<3h',C.string_at(node,6))) if node else None,
                        driver=string(C.addressof(source_buf)) if source_obj else None,driver_site=list(struct.unpack('<4h',C.string_at(source_phy+0x10,8))) if source_phy else None,
                        native_pin_type=C.c_int.from_address(pin+0x48).value))
            if phy:
                _,rr,cc,slot=row['physical_header'];aggregate=C.c_uint64.from_address(getgrid(chip,rr,cc)+0x70).value
                row['physical_mux_state']=C.string_at(phy+0x18,8).hex();row['physical_mux_cached_logic_matches']=C.c_uint64.from_address(phy+0x18).value==obj;row['cfu_flags']=C.string_at(aggregate+0x318,0x48).hex()
                row['cls_attributes']=[list(struct.unpack('<19h',C.string_at(C.c_uint64.from_address(aggregate+0x360+8*j).value+6,38))) for j in range(4)]
        if typ=='.?AVAlu@NETLIST@@':
            row['alu_index']=fn('?getAluIdx@Alu@NETLIST@@QEAAHXZ',C.c_int,[vp])(obj)
            row['virtual_flag']=fn('?getAluVirFlag@Alu@NETLIST@@QEAAEXZ',C.c_ubyte,[vp])(obj)
            buf=C.create_string_buffer(32);fn('?getInitValue@Alu@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])(obj,C.addressof(buf));row['alu_init']=string(C.addressof(buf))
            row['pins']=[]
            alu_pin=fn('?getOnePin@Alu@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint]);direct=fn('?isDirectConn@Pin@NETLIST@@QEAA_NPEAVglobalMem@@@Z',C.c_bool,[vp,vp])
            netname=fn('?getName@Net@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
            for index in range(6):
                pin=alu_pin(obj,index)
                if not pin:continue
                known_pins[pin]=dict(instance=row['name'],pin_index=index,kind='ALU')
                node=C.c_uint64.from_address(pin+0x18).value;net=C.c_uint64.from_address(pin+8).value;netbuf=C.create_string_buffer(32)
                if net:netname(net,C.addressof(netbuf))
                row['pins'].append(dict(index=index,node=list(struct.unpack('<3h',C.string_at(node,6))) if node else None,
                    net=string(C.addressof(netbuf)) if net else None,direct=bool(direct(pin,mem)) if index==3 else None))
            if phy:
                _,rr,cc,slot=row['physical_header'];grid=getgrid(chip,rr,cc);aggregate=C.c_uint64.from_address(grid+0x70).value
                row['cfu_luts']=[dict(slot=i,init=C.c_ushort.from_address(C.c_uint64.from_address(aggregate+8*i).value+0x20).value,
                    mode=C.c_ubyte.from_address(C.c_uint64.from_address(aggregate+8*i).value+0x22).value) for i in range(8)]
                row['cfu_used_flags']=list(C.string_at(aggregate+0x318,0x38))
                row['cfu_state_2a0_360']=C.string_at(aggregate+0x2a0,0xc0).hex()
                row['physical_alu_state']=C.string_at(phy+0x18,0x30).hex()
                row['physical_alu_cached_logic_matches']=C.c_uint64.from_address(phy+0x20).value==obj
        if typ=='.?AVLut4@NETLIST@@':
            # Field locations verified against the exported string getters.
            row['init_value']=string(obj+0xf0)
            row['routed_init_value']=string(obj+0x110)
            row['physical_init_word']=C.c_ushort.from_address(phy+0x20).value if phy else None
            if phy:
                _,rr,cc,slot=row['physical_header'];aggregate=C.c_uint64.from_address(getgrid(chip,rr,cc)+0x70).value
                row['physical_input_mode']=C.c_ubyte.from_address(phy+0x22).value
                row['cfu_cls_attributes']=[dict(group=j,header=list(struct.unpack('<3h',C.string_at(C.c_uint64.from_address(aggregate+0x360+8*j).value,6))),
                    attributes=list(struct.unpack('<19h',C.string_at(C.c_uint64.from_address(aggregate+0x360+8*j).value+6,38)))) for j in range(4)]
                row['cfu_register_used_flags']=list(C.string_at(aggregate+0x318,8))
                row['cfu_lut_used_flags']=list(C.string_at(aggregate+0x326,8))
            lut_objects.append((obj,row))
            for pin_index in (0,1,2,3,8):
                pin=lutpin(obj,pin_index)
                if pin:known_pins[pin]=dict(instance=row['name'],pin_index=pin_index,kind='LUT')
        if typ=='.?AVReg@NETLIST@@':
            _,rr,cc,slot=row['physical_header'];grid=getgrid(chip,rr,cc)
            if C.c_int.from_address(grid+0x18).value==5:
                aggregate=C.c_uint64.from_address(grid+0x70).value
                companion=C.c_uint64.from_address(aggregate+8*slot).value
                if companion:
                    row['ff_companion_lut']=dict(header=list(struct.unpack('<4H',C.string_at(companion+0x10,8))),
                        init_word=C.c_ushort.from_address(companion+0x20).value,
                        input_mode=C.c_ubyte.from_address(companion+0x22).value)
                row['cfu_cls_attributes']=[]
                for group in range(4):
                    block=C.c_uint64.from_address(aggregate+0x360+group*8).value
                    row['cfu_cls_attributes'].append(dict(group=group,header=list(struct.unpack('<3h',C.string_at(block,6))),
                        attributes=list(struct.unpack('<19h',C.string_at(block+6,38)))))
                row['cfu_register_used_flags']=list(C.string_at(aggregate+0x318,8))
                row['cfu_lut_used_flags']=list(C.string_at(aggregate+0x326,8))
            reg=getreg(obj);row['register_class']=classname(reg);row['register_ports']=[]
            if reg and classname(reg)=='.?AVreg@NETLIST@@':
                for offset in (0x18,0x20,0x28,0x30,0x38):
                    p=C.c_uint64.from_address(reg+offset).value
                    triple=list(struct.unpack('<3h',C.string_at(p,6))) if p else None
                    row['register_ports'].append(dict(offset=hex(offset),node=triple,name=names.get(triple[2]) if triple else None))
            row['routed_pin_nodes']=[]
            for index in range(8):
                pin=getpin(obj,index)
                if not pin: continue
                known_pins[pin]=dict(instance=row['name'],pin_index=index,kind='FF')
                nodes=(C.c_uint64*3)();ok=pinnodes(pin,C.addressof(nodes),mem)
                a,b,cap=nodes
                if not a: points=[]
                else:
                    if not a<=b<=cap or (b-a)%8 or (b-a)//8>1000: raise ValueError('Invalid native pin vector')
                    points=[]
                    for j in range((b-a)//8):
                        np=C.c_uint64.from_address(a+8*j).value
                        triple=list(struct.unpack('<3h',C.string_at(np,6)))
                        points.append(dict(node=triple,name=names.get(triple[2])))
                row['routed_pin_nodes'].append(dict(pin_index=index,query_return=ok,nodes=points))
        rows.append(row)
    (out/'primitive_bindings.json').write_text(json.dumps(rows,indent=2))
    print('CAPTURED_PRIMITIVES',len(rows),flush=True)
    if capture_routes:
        # Global node flags also include default switch selections. Restrict
        # observation to real logical endpoints and trace toward their driver.
        netname=fn('?getName@Net@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
        def ptr(address):return C.c_uint64.from_address(address).value
        def triple(node):return list(struct.unpack('<3h',C.string_at(node,6)))
        nets={};pin_seen=set()
        for i in range((end-start)//8):
            obj=ptr(start+8*i);buf=C.create_string_buffer(32);getname(obj,C.addressof(buf));instance_name=string(C.addressof(buf))
            virtual_getpin=C.CFUNCTYPE(vp,vp,C.c_uint)(ptr(ptr(obj)+0x30))
            for pin_index in range(16):
                pin=virtual_getpin(obj,pin_index)
                if not pin or pin in pin_seen:continue
                pin_seen.add(pin);net=ptr(pin+8)
                if not net:continue
                driver=ptr(net+0x18);driver_instance=ptr(driver+0x10) if driver else None
                driver_type=gettype(driver_instance) if driver_instance else None
                nv=(C.c_uint64*3)();query=pinnodes(pin,C.addressof(nv),mem)
                if not nv[0]:points=[]
                else:
                    if not nv[0]<=nv[1]<=nv[2] or (nv[1]-nv[0])%8 or (nv[1]-nv[0])//8>1000:raise ValueError('Invalid endpoint vector')
                    points=[ptr(p) for p in range(nv[0],nv[1],8)]
                if net not in nets:
                    nb=C.create_string_buffer(32);netname(net,C.addressof(nb))
                    nets[net]=dict(name=string(C.addressof(nb)),driver_type=driver_type,sources=set(),sinks=[])
                info=dict(instance=instance_name,pin_index=pin_index,query_return=query)
                if pin==driver:nets[net]['sources'].update(points)
                elif points:nets[net]['sinks'].append((info,points))
        getdes=fn('?getAllDesPin@Net@NETLIST@@QEAAAEBV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp])
        get_pin_name=fn('?getPinName@Pin@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
        get_report_name=fn('?getPinNameForRpt@Pin@NETLIST@@QEAAXAEAV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])
        via_lutbuf=fn('?isViaLutbuf@Pin@NETLIST@@QEAA_NPEAVglobalMem@@@Z',C.c_bool,[vp,vp])
        logical_endpoint_pins={}
        def physical_points(pin):
            nv=(C.c_uint64*3)();ok=pinnodes(pin,C.addressof(nv),mem)
            if not nv[0]:return ok,[]
            if not nv[0]<=nv[1]<=nv[2] or (nv[1]-nv[0])%8 or (nv[1]-nv[0])//8>1000:raise ValueError('Invalid native net endpoint vector')
            return ok,[ptr(p) for p in range(nv[0],nv[1],8)]
        # I/O primitive pin enums do not share the LUT/FF enum range. Use
        # each actual net's driver and full destination vector for coverage.
        for net_pointer,net in nets.items():
            driver=ptr(net_pointer+0x18)
            if driver:net['sources']=set(physical_points(driver)[1])
            vectorp=getdes(net_pointer);vb,ve,vc=ptr(vectorp),ptr(vectorp+8),ptr(vectorp+16)
            if vb and (not vb<=ve<=vc or (ve-vb)%8 or (ve-vb)//8>1000000):raise ValueError('Invalid native sink vector')
            net['sinks']=[]
            for address in range(vb,ve,8) if vb else []:
                pin=ptr(address);obj=ptr(pin+0x10);buf=C.create_string_buffer(32);getname(obj,C.addressof(buf))
                pinbuf=C.create_string_buffer(32);get_pin_name(pin,C.addressof(pinbuf))
                reportbuf=C.create_string_buffer(32);C.c_uint64.from_address(C.addressof(reportbuf)+24).value=15
                get_report_name(pin,C.addressof(reportbuf))
                logical_endpoint_pins[string(C.addressof(reportbuf))]=pin
                ok,points=physical_points(pin)
                if points:net['sinks'].append((dict(instance=string(C.addressof(buf)),pin_index=known_pins.get(pin,{}).get('pin_index'),
                    pin_name=string(C.addressof(pinbuf)),report_name=string(C.addressof(reportbuf)),
                    via_lutbuf=bool(via_lutbuf(pin,mem)),native_pin_type=C.c_int.from_address(pin+0x48).value,query_return=ok),points))
        records=[];paths=0;matched=0;observed_nodes={};delay_paths=[]
        for net in nets.values():
            if net['driver_type'] in (166,167):continue
            sources=net['sources'];sinks=[]
            for info,points in net['sinks']:
                for sink in points:
                    node=sink;visited=set();chain=[];status='null_parent'
                    for _ in range(20000):
                        if not node:break
                        if node in visited:status='cycle';break
                        visited.add(node);chain.append(triple(node));observed_nodes[tuple(triple(node))]=node
                        if node in sources:status='driver_reached';break
                        node=ptr(node+0x18)
                    else:status='trace_limit'
                    paths+=1;matched+=status=='driver_reached'
                    sinks.append(dict(**info,status=status,path_sink_to_source=chain))
                    if capture_wire_delays and status=='driver_reached':
                        delay_paths.append((net['name'],info,sink,node,chain))
            if sources or sinks:records.append(dict(name=net['name'],driver_type=net['driver_type'],source_nodes=[triple(n) for n in sources],sinks=sinks))
        result=dict(nets=records,paths=paths,matched_driver_paths=matched,unmatched_driver_paths=paths-matched,
            scope='Post-task parent chains from logical endpoints only. Native coordinate aliases and default/unused switches are not treated as routed design nets.')
        (out/'native_endpoint_routes.json').write_text(json.dumps(result,separators=(',',':')))
        print('CAPTURED_ENDPOINT_PATHS',paths,'MATCHED',matched,flush=True)
        if capture_wire_delays:
            import math
            monitor=fn('?getTmMonitor@globalMem@@QEAAPEAVTmMonitor@STA@@XZ',vp,[vp])(mem)
            if not monitor:raise ValueError('Native STA monitor unavailable')
            wire=fn('?getWiresDly@TmMonitor@STA@@QEAAXPEAMPEAVnode@NETLIST@@@Z',None,[vp,vp,vp])
            net_delay=fn('?getOneNetSinkDlyNew@TmMonitor@STA@@QEAAMPEAVnode@NETLIST@@0_N@Z',C.c_float,[vp,vp,vp,C.c_bool])
            context=ptr(monitor+8);library=ptr(context+0x38);native_context=ptr(context+0x18)
            (out/'timing_mode_state.json').write_text(json.dumps(dict(monitor_bytes=C.string_at(monitor+0x10,16).hex(),
                flow_flag=C.c_int.from_address(monitor+0x14).value,
                context_flags=C.string_at(context+0x200,0x30).hex(),
                bank_vccio=[fn('?getBankVccio@chip@NETLIST@@QEAA?AW4PIO_VALUE@2@F@Z',C.c_int,[vp,C.c_short])(chip,b) for b in range(16)]),indent=2))
            device_kind=C.c_int.from_address(native_context+0x180).value
            selector=C.c_int.from_address(library+0x18).value
            if device_kind!=70 or selector not in (0,1,2):raise ValueError('Unreviewed native wire timing context')
            width=C.c_short.from_address(library+0x1c).value;group_count=C.c_short.from_address(library+0x1e).value
            id_count=C.c_short.from_address(library+0x20).value
            if (width,group_count,id_count)!=(4,9,13):raise ValueError('Unreviewed native cell library layout')
            groups=[]
            for group_index in range(group_count):
                group=ptr(ptr(library+8)+group_index*8)
                row_count=C.c_short.from_address(group+0x10).value;pin_count=C.c_short.from_address(group+0x12).value
                if not 0<row_count<1024 or not 0<pin_count<1024:raise ValueError('Invalid native cell group counts')
                cell_rows=[]
                for row_index in range(row_count):
                    row_pointer=ptr(ptr(group)+row_index*8);length=C.c_short.from_address(row_pointer).value
                    if length!=12:raise ValueError('Unexpected native cell row width')
                    cell_rows.append(list(struct.unpack('<12f',C.string_at(ptr(row_pointer+8),48))))
                groups.append(dict(index=group_index,pin_ids=list(struct.unpack('<'+str(pin_count)+'h',C.string_at(ptr(group+8),pin_count*2))),rows=cell_rows))
            cell_library=dict(groups=groups,id_map=list(struct.unpack('<13h',C.string_at(ptr(library+0x10),26))),
                active_selector=selector,width=width,dll_sha256=EXPECTED,open_chip_database_used=False)
            (out/'native_cell_library.json').write_text(json.dumps(cell_library,indent=2))
            # The native dispatch receives the active selector explicitly.
            # Query the three stored four-float groups without changing it.
            wire_at=C.CFUNCTYPE(None,vp,vp,vp,C.c_int,C.c_int)(dll._handle+0x14733b0)
            node_records=[]
            for coord,node in sorted(observed_nodes.items()):
                # Native callers zero the vector: some branch types leave
                # components untouched. Preserve that calling convention.
                values=(C.c_float*4)();wire(monitor,C.addressof(values),node)
                if not all(math.isfinite(v) for v in values):raise ValueError('Incomplete native wire-delay result')
                sentinel=(C.c_float*4)(*[float('nan')]*4);wire(monitor,C.addressof(sentinel),node)
                by_selector=[]
                for index in range(3):
                    selected=(C.c_float*4)();wire_at(library,C.addressof(selected),node,index,device_kind)
                    if not all(math.isfinite(v) for v in selected):raise ValueError('Invalid selected wire delay')
                    by_selector.append(list(selected))
                if by_selector[selector]!=list(values):raise ValueError('Explicit selector dispatch disagrees with native monitor')
                parent=ptr(node+0x18)
                node_records.append(dict(node=list(coord),parent=triple(parent) if parent else None,
                    values=list(values),values_by_selector=by_selector,finite_after_nan_initialization=[math.isfinite(v) for v in sentinel],
                    native_node_bytes=C.string_at(node,0x30).hex()))
            path_records=[]
            endpoint_adjust=C.CFUNCTYPE(None,vp,vp,vp,vp)(dll._handle+0x12d8200)
            local_delay=C.CFUNCTYPE(None,vp,vp,C.c_int,C.c_int,C.c_int)(dll._handle+0x14537f0)
            lut_queries=[]
            for pin in range(4):
                for bank in range(2):
                    for index in range(3):
                        value=(C.c_float*4)();local_delay(library,C.addressof(value),pin,index,bank)
                        lut_queries.append(dict(input=pin,slot_group=bank,selector=index,values=list(value)))
            cell_library['lut_input_queries']=lut_queries
            (out/'native_cell_library.json').write_text(json.dumps(cell_library,indent=2))
            for net,info,sink,source,chain in delay_paths:
                maximum=net_delay(monitor,source,sink,True);minimum=net_delay(monitor,source,sink,False)
                if not math.isfinite(maximum) or not math.isfinite(minimum):raise ValueError('Invalid native path delay')
                adjustment=(C.c_float*4)();endpoint_adjust(context,C.addressof(adjustment),logical_endpoint_pins[info['report_name']],sink)
                local_by_selector=[];node_id=triple(sink)[2]
                for index in range(3):
                    local=(C.c_float*4)()
                    if info['via_lutbuf'] and 0<=node_id<32:
                        local_delay(library,C.addressof(local),node_id%4,index,int(node_id>=24))
                    local_by_selector.append(list(local))
                if list(adjustment)!=local_by_selector[selector]:raise ValueError('Local LUT compensation rule disagrees with native helper')
                path_records.append(dict(net=net,**info,source=triple(source),sink=triple(sink),path_sink_to_source=chain,
                    local_adjustment=list(adjustment),local_by_selector=local_by_selector,
                    maximum=maximum,minimum=minimum,source_has_parent=bool(ptr(source+0x18))))
            result=dict(nodes=node_records,paths=path_records,active_selector=selector,device_kind=device_kind,sta_endpoints=sta_endpoints,
                vector_slot_semantics_verified=False,corner_verified=False,
                report_units_verified=False,own_pnr_result=False,
                scope='Read-only native STA node and parent-chain query samples. No general timing model or signoff is implied.')
            (out/'native_wire_delays.json').write_text(json.dumps(result,indent=2))
            print('CAPTURED_WIRE_DELAYS',len(node_records),'PATHS',len(path_records),flush=True)
    if encode_probe:
        target,record=lut_objects[0]
        fs=out/'probe.fs'
        if not fs.exists(): raise ValueError('Native bitstream absent')
        shutil.copyfile(fs,out/'encoding_baseline.fs')
        getinit=fn('?getInitValueForRouted@Lut4@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
        setinit=fn('?setInitValueForRouted@Lut4@NETLIST@@QEAAXV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])
        encode=fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])
        if physical_init:
            # The native table-5 handler reads physical lut4+0x20 as the
            # 16-bit INIT word. Regenerate an unmodified baseline first:
            # the post-task state does not reproduce the initial task output.
            original_fs=out/'encoding_task_output.fs';shutil.copyfile(fs,original_fs)
            if encode(mem):raise ValueError('Unmodified post-task encoder failed')
            shutil.copyfile(fs,out/'encoding_baseline.fs')
            if init_sweep:
                targets=[(getphy(obj),record) for obj,record in lut_objects]
                originals=[C.c_ushort.from_address(p+0x20).value for p,_ in targets]
                captures=[]
                for mask in (0xffff,0xaaaa,0xcccc,0xf0f0,0xff00):
                    for (p,_),value in zip(targets,originals):C.c_ushort.from_address(p+0x20).value=value^mask
                    if encode(mem):raise ValueError('LUT sweep encoder failed')
                    path=out/f'encoding_mask_{mask:04x}.fs.gz'
                    with fs.open('rb') as src,gzip.open(path,'wb',compresslevel=1) as dst:shutil.copyfileobj(src,dst)
                    captures.append(dict(mask=mask,file=path.name,sha256=sha(path)))
                    print('LUT_INIT_MASK_CAPTURED',hex(mask),flush=True)
                for (p,_),value in zip(targets,originals):C.c_ushort.from_address(p+0x20).value=value
                if encode(mem):raise ValueError('LUT sweep restore failed')
                shutil.copyfile(fs,out/'encoding_restored.fs')
                report=dict(targets=[dict(name=r['name'],physical_header=r['physical_header'],original_init=v) for (_,r),v in zip(targets,originals)],
                    captures=captures,placement_routing_rerun_during_mutation=False,open_chip_database_used=False,
                    baseline_sha256=sha(out/'encoding_baseline.fs'),restored_sha256=sha(out/'encoding_restored.fs'),dll_sha256=EXPECTED,
                    scope='Physical LUT INIT differential calibration in post-task state; not a full routed-design or bitstream signoff.')
                (out/'lut_init_sweep.json').write_text(json.dumps(report,indent=2))
                fn('?closeLogFile@@YAXXZ',None,[])()
                return
            phy=getphy(target);word=C.c_ushort.from_address(phy+0x20);value=word.value
            word.value=value^1
            if encode(mem):raise ValueError('Physical INIT encoder failed')
            shutil.copyfile(fs,out/'encoding_variant.fs')
            word.value=value
            if encode(mem):raise ValueError('Physical INIT restoration failed')
            shutil.copyfile(fs,out/'encoding_restored.fs')
            report=dict(instance=record['name'],physical_header=record['physical_header'],physical_init_before=value,physical_init_after=value^1,
                        vlg_flag=fn('?getVlgFlag@globalMem@@QEAA_NXZ',C.c_bool,[vp])(mem),
                        placement_routing_rerun_during_mutation=False,field_rva_evidence='0x52fea4',
                        scope='Post-task physical INIT differential only; baseline does not certify original routed design or hardware.')
            (out/'encoding_probe.json').write_text(json.dumps(report,indent=2))
            print('PHYSICAL_ENCODER_PROBE',json.dumps(report),flush=True)
            fn('?closeLogFile@@YAXXZ',None,[])()
            return
        original=record['routed_init_value']
        if not re.fullmatch(r"16'[hH][0-9a-fA-F]{4}",original):
            raise ValueError('Unrecognized native INIT representation: '+repr(original))
        def change_last_digit(value):
            # Getter allocates with the native CRT; by-value setter owns and
            # frees this temporary. Never pass a Python-owned long-string heap.
            b=C.create_string_buffer(32);getinit(target,C.addressof(b));p=C.addressof(b)
            if C.c_uint64.from_address(p+24).value>=16:p=C.c_uint64.from_address(p).value
            C.c_char.from_address(p+len(original)-1).value=value.encode()
            setinit(target,C.addressof(b))
        change_last_digit(format(int(original[-1],16)^1,'x'))
        mutated=string(target+0x110)
        if encode(mem):raise ValueError('Variant encoder failed')
        shutil.copyfile(fs,out/'encoding_variant.fs')
        change_last_digit(original[-1])
        if encode(mem):raise ValueError('Restore encoder failed')
        shutil.copyfile(fs,out/'encoding_restored.fs')
        report=dict(instance=record['name'],physical_header=record['physical_header'],original=original,mutated=mutated,
                    baseline_sha256=sha(out/'encoding_baseline.fs'),variant_sha256=sha(out/'encoding_variant.fs'),restored_sha256=sha(out/'encoding_restored.fs'),
                    placement_routing_rerun_during_mutation=False,scope='Native encoder differential oracle only; deliberately mutated calibration logic, not a usable design result.')
        (out/'encoding_probe.json').write_text(json.dumps(report,indent=2))
        print('ENCODER_PROBE',json.dumps(report),flush=True)
    fn('?closeLogFile@@YAXXZ',None,[])()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--worker',action='store_true');ap.add_argument('--encode-probe',action='store_true');ap.add_argument('--physical-init',action='store_true');ap.add_argument('--init-sweep',action='store_true');ap.add_argument('--bit',action='store_true');ap.add_argument('--capture-routes',action='store_true')
    ap.add_argument('--capture-wire-delays',action='store_true')
    ap.add_argument('--hard-resources',action='store_true')
    ap.add_argument('--hard-timing',action='store_true')
    ap.add_argument('--clock-chains',action='store_true')
    ap.add_argument('--sta-endpoints',action='store_true')
    ap.add_argument('--extended-timing',action='store_true')
    ap.add_argument('--mux-timing',action='store_true')
    ap.add_argument('--output-iob-registers',action='store_true')
    ap.add_argument('--ioreg-capture',action='store_true')
    ap.add_argument('--clock-policy',choices=('original','auto'),default='original')
    ap.add_argument('--netlist',type=Path);ap.add_argument('--cst',type=Path);ap.add_argument('--sdc',type=Path)
    args=ap.parse_args();out=args.out.resolve()
    if args.sta_endpoints:args.capture_wire_delays=True
    if args.capture_wire_delays:args.capture_routes=True
    if args.physical_init and not args.encode_probe:ap.error('--physical-init requires --encode-probe')
    if args.init_sweep and not args.physical_init:ap.error('--init-sweep requires --physical-init')
    if args.worker: return worker(out,args.encode_probe,args.physical_init,args.init_sweep,args.capture_routes,args.capture_wire_delays,args.sta_endpoints)
    prepare(out)
    if args.extended_timing:(out/'extended_timing_requested.json').write_text('{}')
    if args.hard_resources:(out/'hard_resources_requested.json').write_text('{}')
    if args.hard_timing:(out/'hard_timing_requested.json').write_text('{}')
    if args.clock_chains:(out/'clock_chains_requested.json').write_text('{}')
    if args.mux_timing:(out/'mux_timing_requested.json').write_text('{}')
    if args.ioreg_capture:(out/'ioreg_capture_requested.json').write_text('{}')
    source=args.netlist or ROOT/'results/synthesis_wide_int157_001/lut4/05_netlist/mapped.vg'
    cst=args.cst or ROOT/'results/int2float_clock_ablation_001/lut4.cst'
    sdc=args.sdc or ROOT/'results/fullio_closure_003/int_lut4_local155_edge.sdc'
    for src,name in [(source,'probe.vg'),(cst,'project.cst'),(sdc,'probe.sdc')]: shutil.copyfile(src,out/name)
    if args.clock_policy=='auto':
        original=(out/'project.cst').read_text()
        changed,count=re.subn(r'^CLOCK_LOC\s+"ng_internal_clk"\s+LOCAL_CLOCK;\s*$', '', original,flags=re.M)
        if count!=1:raise ValueError('Expected exactly the retained internal local-clock directive')
        if [s for s in original.splitlines() if s.startswith(('IO_LOC','IO_PORT'))]!=[s for s in changed.splitlines() if s.startswith(('IO_LOC','IO_PORT'))]:
            raise ValueError('Clock calibration changed I/O constraints')
        (out/'project.cst').write_text(changed)
    cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+f'-cst {(out/"project.cst").as_posix()}\n')
    if args.output_iob_registers:
        text=cmd.read_text()
        if text.splitlines().count('-oreg_not_in_iob')!=1:raise ValueError('Unexpected output IOB policy')
        cmd.write_text('\n'.join(line for line in text.splitlines() if line!='-oreg_not_in_iob')+'\n')
    if args.encode_probe or args.bit:cmd.write_text(cmd.read_text()+'-bit\n')
    shutil.copyfile(__file__,out/'extractor_source.py.txt')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';started=time.monotonic();rc=None;timeout=False
    with (out/'worker.log').open('w') as log:
        try:
            rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)]+(['--encode-probe'] if args.encode_probe else [])+(['--physical-init'] if args.physical_init else [])+(['--init-sweep'] if args.init_sweep else [])+(['--capture-routes'] if args.capture_routes else [])
                +(['--capture-wire-delays'] if args.capture_wire_delays else [])+(['--sta-endpoints'] if args.sta_endpoints else []),cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=180).returncode
        except subprocess.TimeoutExpired: timeout=True
    result=dict(status='captured' if rc==0 and (out/'primitive_bindings.json').exists() else 'failed',
        returncode=rc,timeout=timeout,wall_seconds=time.monotonic()-started,dll_sha256=EXPECTED,
        source_sha256=sha(source),cst_sha256=sha(out/'project.cst'),original_cst_sha256=sha(cst),clock_policy=args.clock_policy,
        sdc_sha256=sha(sdc),open_chip_database_used=False,
        vendor_placement_executed=True,vendor_routing_executed=True,own_pnr_result=False,device_programmed=False,sta_endpoints=args.sta_endpoints,
        output_iob_registers=args.output_iob_registers)
    if result['status']=='captured': result['bindings_sha256']=sha(out/'primitive_bindings.json')
    if args.hard_resources:
        if (out/'hard_resource_bindings.json').exists():result['hard_resources_sha256']=sha(out/'hard_resource_bindings.json')
        else:result['status']='failed'
    if args.hard_timing:
        if (out/'hard_timing_index.json').exists():result['hard_timing_sha256']=sha(out/'hard_timing_index.json')
        else:result['status']='failed'
        result['hard_timing_helper_sha256']=sha(ROOT/'scripts/native_hard_timing.py')
    if args.clock_chains:
        if (out/'native_clock_chains.json').exists():result['clock_chains_sha256']=sha(out/'native_clock_chains.json')
        else:result['status']='failed'
    if args.ioreg_capture:
        if (out/'ioreg_state.json').exists():result['ioreg_state_sha256']=sha(out/'ioreg_state.json')
        else:result['status']='failed'
    if args.extended_timing:
        if (out/'extended_timing_queries.json').exists():result['extended_timing_sha256']=sha(out/'extended_timing_queries.json')
        else:result['status']='failed'
    if args.mux_timing:
        if (out/'mux_timing_queries.json').exists():result['mux_timing_sha256']=sha(out/'mux_timing_queries.json')
        else:result['status']='failed'
    if args.capture_routes:
        if (out/'native_endpoint_routes.json').exists():result['endpoint_routes_sha256']=sha(out/'native_endpoint_routes.json')
        else:result['status']='failed'
    if args.capture_wire_delays:
        if (out/'native_wire_delays.json').exists():result['wire_delays_sha256']=sha(out/'native_wire_delays.json')
        else:result['status']='failed'
        if (out/'native_cell_library.json').exists():result['cell_library_sha256']=sha(out/'native_cell_library.json')
        result['timing_file_sha256']=sha(BIN.parent/'share/device/GW5AST-138C/GW5AST-138C.tm')
    if args.encode_probe and (out/'encoding_probe.json').exists():
        from audit_native_encoding_probe import audit
        check=audit(out);(out/'encoding_audit.json').write_text(json.dumps(check,indent=2))
        result['encoding_audit_status']=check['status']
        result['usable_for_encoding_inference']=check['usable_for_encoding_inference']
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']!='captured': raise SystemExit(1)


if __name__=='__main__': main()
