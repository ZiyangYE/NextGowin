"""Observe pinned native hard-cell timing dispatch; not routed signoff."""
import ctypes as C
import json,math


def capture_one(native,obj,resource,out,full=False):
    vp=C.c_void_p;fn=native.fn;ptr=native.ptr;base=native.dll._handle
    monitor=fn('?getTmMonitor@globalMem@@QEAAPEAVTmMonitor@STA@@XZ',vp,[vp])(native.mem)
    context=ptr(monitor+8);old_library=ptr(context+0x30);library=ptr(context+0x38)
    if not old_library or not library:raise ValueError('Hard timing libraries are not initialized')
    cls='NewDsp' if resource=='dsp' else 'Bsram'
    label_fn=fn('?getPinNameForRpt@Pin@NETLIST@@QEAAXAEAV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])
    pins={}
    for direction in ('In','Out'):
        vector=(C.c_uint64*3)();fn(f'?getAll{direction}Pins@{cls}@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(obj,C.addressof(vector))
        if not vector[0]<=vector[1]<=vector[2] or vector[1]-vector[0]>32768:raise ValueError('Invalid hard timing pin vector')
        for a in range(vector[0],vector[1],8):
            pin=ptr(a);buffer=C.create_string_buffer(32);address=C.addressof(buffer);C.c_uint64.from_address(address+24).value=15;label_fn(pin,address)
            size=ptr(address+16)
            if size>4096:raise ValueError('Hard timing pin label too long')
            label=C.string_at(ptr(address) if ptr(address+24)>=16 else address,size).decode().rsplit('.',1)[-1]
            pins[label]=pin
    records=[]
    if resource=='dsp':
        # Native cell dispatcher 0x13ab66d uses the +0x30 library for subtype 185.
        query=C.CFUNCTYPE(None,vp,vp,vp,vp,C.c_bool)(base+0x145f810)
        pairs=[(a,b) for a in ('A[0]','A[26]','B[0]','B[17]') for b in ('DOUT[0]','DOUT[17]','DOUT[47]')]
        if full:pairs=[(a,b) for a in sorted(pins) if a.startswith(('A[','B[')) for b in sorted(pins) if b.startswith('DOUT[')]
        profiles=[(None,False,None)]
    else:
        # Native dispatcher 0x13ab4e5 selects this library with selector and
        # two edge-kind flags. Capture flags without prematurely naming them.
        query=C.CFUNCTYPE(None,vp,vp,vp,vp,C.c_int,C.c_bool,C.c_bool)(base+0x1445b00)
        targets=[p for p in ('ADA[0]','ADB[0]','AD[0]','DI[0]','DIA[0]','DIB[0]','DO[0]','DOA[0]','DOB[0]','WREA','WREB','CEA','CEB','RESETA','RESETB','OCEA','OCEB') if p in pins]
        if full:targets=[p for p in sorted(pins) if p not in ('CLKA','CLKB')]
        pairs=[(clock,p) for clock in ('CLKA','CLKB') if clock in pins for p in targets]
        profiles=[(s,a,b) for s in range(3) for a in (False,True) for b in (False,True)]
    for source,destination in pairs:
        if source not in pins or destination not in pins:raise ValueError('Missing hard timing sample pin')
        for selector,flag_a,flag_b in profiles:
            values=(C.c_float*4)(*[float('nan')]*4)
            if resource=='dsp':query(old_library,C.addressof(values),pins[source],pins[destination],flag_a)
            else:query(library,C.addressof(values),pins[source],pins[destination],selector,flag_a,flag_b)
            finite=all(math.isfinite(v) for v in values)
            records.append(dict(source=source,destination=destination,selector=selector,flag_a=flag_a,flag_b=flag_b,
                values=list(values) if finite else None,complete=finite))
    edge_records=[];seen=set();labels={ptr(pin+0x20):name for name,pin in pins.items() if ptr(pin+0x20)}
    path_query=C.CFUNCTYPE(vp,vp)(base+0x13be8c0)
    wanted=set(pairs)|{(b,a) for a,b in pairs}
    for node,label in labels.items():
        view=path_query(node)
        if not view:continue
        start,end=ptr(view+0x28),ptr(view+0x30)
        if not start<=end or end-start>8*20000 or (end-start)%8:raise ValueError('Invalid native timing adjacency')
        for address in range(start,end,8):
            edge=ptr(address)
            if not edge or edge in seen:continue
            source,destination=labels.get(ptr(edge+0x10)),labels.get(ptr(edge+0x18))
            if (source,destination) not in wanted:continue
            seen.add(edge);values=[]
            for offset in (0,8):
                matrix=ptr(edge+offset)
                if not matrix:raise ValueError('Native edge lacks delay matrix')
                values.append(list((C.c_float*8).from_address(matrix)))
            if not all(math.isfinite(v) for a in values for v in a):raise ValueError('Native hard edge has nonfinite delay')
            edge_records.append(dict(source=source,destination=destination,kind=C.c_ubyte.from_address(edge+0x38).value,matrices=values))
    result=dict(resource=resource,full_data_pins=full,query_count=len(records),queries=records,actual_edges=edge_records,scope='Native cell-table queries and existing STA edges; mode legality, pulse-width coverage and routing require separate checks. Full DSP data coverage means A/B to DOUT in the current bypass fixture.')
    (out/'hard_timing_queries.json').write_text(json.dumps(result,indent=2));return result


def capture_all(native,out,vendor_pnr_executed=True):
    vp=C.c_void_p;fn=native.fn;ptr=native.ptr
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(native.mem);vector=(C.c_uint64*3)()
    fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vector))
    gettype=fn('?getType@Instance@NETLIST@@QEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp])
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    rows=[]
    from ownpr_image_storage import sha
    for address in range(vector[0],vector[1],8):
        obj=ptr(address);typ=gettype(obj)
        if typ not in (4,5):continue
        buf=C.create_string_buffer(32);p=C.addressof(buf);getname(obj,p);n=ptr(p+16)
        if n>4096:raise ValueError('Invalid hard instance name')
        name=C.string_at(ptr(p) if ptr(p+24)>=16 else p,n).decode()
        folder=out/'hard_timing_instances'/f'instance_{len(rows):03d}';folder.mkdir(parents=True)
        record=capture_one(native,obj,'dsp' if typ==4 else 'bsram',folder,full=True)
        rows.append(dict(instance=name,file=str((folder/'hard_timing_queries.json').relative_to(out)),sha256=sha(folder/'hard_timing_queries.json'),queries=record['query_count'],actual_edges=len(record['actual_edges'])))
    if not rows:raise ValueError('No native hard timing instances')
    result=dict(instances=rows,vendor_pnr_executed=vendor_pnr_executed,
        scope='Post-STA native cell queries and actual timing edges; placement/routing origin is recorded separately.')
    (out/'hard_timing_index.json').write_text(json.dumps(result,indent=2));return result
