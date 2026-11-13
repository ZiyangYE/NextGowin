"""Calibrate the stock inverter-fold pass on retained vendor physical state.

No placement/routing or hardware configuration is changed deliberately. The
caller must independently compare physical state and full native timing.
Local semantic checks reject transformations beyond pin inversion/rewiring.
"""
import ctypes as C


def normalize(fn,mem,engine='stock'):
    vp=C.c_void_p;ptr=lambda p:C.c_uint64.from_address(p).value
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(mem)
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    hasinv=fn('?hasInvertInPin@Pin@NETLIST@@QEAA_NXZ',C.c_bool,[vp])
    label_fn=fn('?getPinNameForRpt@Pin@NETLIST@@QEAAXAEAV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])
    inv_input=fn('?getOneInPin@Inv@NETLIST@@QEAAPEAVPin@2@XZ',vp,[vp])
    getinputs=fn('?getAllInPins@Instance@NETLIST@@QEAAXAEAV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@@Z',None,[vp,vp])
    getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])
    def vector(p):
        first,last,cap=(ptr(p+i) for i in (0,8,16))
        if not first:return []
        if not first<=last<=cap or (last-first)%8 or (last-first)//8>1000000:raise ValueError('Invalid native vector')
        return [ptr(x) for x in range(first,last,8)]
    def string(p):
        n=ptr(p+16)
        if n>4096:raise ValueError('Invalid native string')
        return C.string_at(ptr(p) if ptr(p+24)>=16 else p,n).decode()
    def name(obj):
        b=C.create_string_buffer(32);getname(obj,C.addressof(b));return string(C.addressof(b))
    def class_name(obj):
        if not obj:return None
        vt=ptr(obj);locator=ptr(vt-8);image_base=locator-C.c_uint32.from_address(locator+20).value
        if C.c_uint32.from_address(locator).value!=1:raise ValueError('Unsupported native RTTI layout')
        type_rva=C.c_uint32.from_address(locator+12).value
        return C.string_at(image_base+type_rva+16,512).split(b'\0',1)[0].decode('ascii')
    def resolve(pin):
        annotation=bool(hasinv(pin));parity=annotation;seen=set();chain=[];objects=[];physical=[];input_annotations=[]
        def record(driver):return dict(driver=driver,inverted=parity,inverter_chain=chain,native_inversion_annotation=annotation,inverter_chain_objects=objects,inverter_chain_physical=physical,inverter_input_annotations=input_annotations)
        while True:
            net=ptr(pin+8);driver=ptr(net+0x18) if net else 0
            if not driver:return record(None)
            obj=ptr(driver+0x10)
            if class_name(obj)!='.?AVInv@NETLIST@@':return record(driver)
            if obj in seen or len(seen)>=64:raise ValueError('Cyclic/excessive native inverter chain')
            seen.add(obj);chain.append(name(obj));objects.append(obj);physical.append(bool(getphy(obj)));parity=not parity;pin=inv_input(obj)
            if not pin:raise ValueError('Native inverter has no input pin')
            input_annotations.append(bool(hasinv(pin)));parity^=bool(hasinv(pin))
    def capture():
        v=(C.c_uint64*3)();fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(v))
        sinks={};modes={};virtual=[]
        for obj in set(vector(C.addressof(v))):
            typ=class_name(obj)
            if typ=='.?AVInv@NETLIST@@':
                virtual.append(dict(name=name(obj),address=obj,physical=bool(getphy(obj))));continue
            if typ=='.?AVLut4@NETLIST@@':
                b=C.create_string_buffer(32)
                fn('?getInitValueForRouted@Lut4@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])(obj,C.addressof(b));modes[str(obj)]=dict(kind=typ,routed_init=string(C.addressof(b)))
            elif typ=='.?AVReg@NETLIST@@':
                b=C.create_string_buffer(32)
                fn('?getInitValue@Reg@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])(obj,C.addressof(b))
                modes[str(obj)]=dict(kind=typ,init=string(C.addressof(b)),subtype=fn('?getSubType@Reg@NETLIST@@UEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp])(obj),inverted_clock=bool(fn('?getInvertFlag@Reg@NETLIST@@QEAA_NXZ',C.c_bool,[vp])(obj)))
            pins=(C.c_uint64*3)();getinputs(obj,C.addressof(pins))
            for pin in vector(C.addressof(pins)):
                b=C.create_string_buffer(32);C.c_uint64.from_address(C.addressof(b)+24).value=15;label_fn(pin,C.addressof(b))
                sinks[str(pin)]=dict(instance=name(obj),instance_class=typ,pin_label=string(C.addressof(b)),**resolve(pin))
        merged=(C.c_uint64*2)()
        fn('?getMergedInvs@Design@NETLIST@@QEAA?AV?$list@PEAVInv@NETLIST@@V?$allocator@PEAVInv@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(design,C.addressof(merged))
        head,count=merged;members=[];addresses=[]
        if count>1000000 or not head:raise ValueError('Invalid merged-inverter list')
        node=ptr(head)
        for _ in range(count):
            if node==head:raise ValueError('Truncated merged-inverter list')
            obj=ptr(node+0x10);addresses.append(obj);members.append(name(obj));node=ptr(node)
        if node!=head:raise ValueError('Merged-inverter list size mismatch')
        return dict(sinks=sinks,modes=modes,inverters=sorted(virtual,key=lambda r:r['name']),merged_inverter_records=sorted(members),merged_inverter_addresses=sorted(addresses))
    before=capture()
    if engine=='stock':
        code=fn('?sepInvInst@VLGPARSE@@YAHPEAVDesign@NETLIST@@@Z',C.c_int,[vp])(design)
        if code:raise ValueError('Stock inverter folding returned '+str(code))
    elif engine=='recorded_controls':
        # Use exported typed netlist mutators, never the global preprocessing
        # pass that deletes real inverter-converted routed LUTs. Preflight all
        # fanouts before any mutation; no unknown pin inversion is introduced.
        from ownpr_inverter_view_proof import CONTROL_PINS
        pending=[];recorded=set(before['merged_inverter_addresses'])
        getout=fn('?getOneOutPin@Inv@NETLIST@@QEAAPEAVPin@2@XZ',vp,[vp])
        getdest=fn('?getAllDesPin@Net@NETLIST@@QEAAAEBV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp])
        for inv in before['inverters']:
            obj=inv['address'];ip=inv_input(obj);op=getout(obj)
            if inv['physical'] or obj not in recorded or not ip or not op:raise ValueError('Unrecorded or physical inverter cannot be folded')
            src,dst=ptr(ip+8),ptr(op+8)
            if not src or not dst or not ptr(src+0x18):raise ValueError('Missing recorded inverter root')
            sinks=vector(getdest(dst))
            if not sinks:raise ValueError('Recorded inverter has no controlled sinks')
            for pin in sinks:
                row=before['sinks'].get(str(pin))
                if not row or row['instance_class']!='.?AVReg@NETLIST@@' or row['pin_label'].rsplit('.',1)[-1] not in CONTROL_PINS or row['inverter_chain_objects']!=[obj] or row['native_inversion_annotation'] is not True or row['inverter_input_annotations']!=[False]:
                    raise ValueError('Recorded inverter has an unproved fanout')
            pending.append((obj,src,dst,sinks))
        remove=fn('?deleteDesPin@Net@NETLIST@@QEAAXPEAVPin@2@@Z',None,[vp,vp])
        append=fn('?addDesPin@Net@NETLIST@@QEAAXPEAVPin@2@@Z',None,[vp,vp])
        bind=fn('?setNet@Pin@NETLIST@@QEAAXPEAVNet@2@@Z',None,[vp,vp])
        for obj,src,dst,sinks in pending:
            for pin in sinks:
                if pin in vector(getdest(src)):raise ValueError('Duplicate destination in root net')
                remove(dst,pin);append(src,pin);bind(pin,src)
            # The original virtual input must not remain an extra timing
            # endpoint on the root net after its output loses all fanout.
            ip=inv_input(obj);remove(src,ip);bind(ip,None)
            # Retain the original merged-inverter registry and object. The
            # restored INV now has zero functional fanout. removeOneInv also
            # erases that registry entry and would destroy the proof witness.
    else:raise ValueError('Unknown inverter normalization engine')
    after=capture();changes=[]
    from ownpr_inverter_view_proof import compare_views
    view_proof=compare_views(before,after);errors=view_proof['errors']
    for pin in before['sinks'].keys()&after['sinks'].keys():
        old,new=before['sinks'][pin],after['sinks'][pin]
        if old!=new:changes.append(dict(pin=pin,before=old,after=new))
    return dict(status='native_inverter_normalization_semantics_preserved' if not errors else 'normalization_rejected',
        native_pass='sepInvInst' if engine=='stock' else 'exported_recorded_control_netlist_mutators',native_pnr_executed_by_pass=False,errors=errors,changes=changes,
        representation_proof=view_proof,
        compared_non_inverter_input_pins=len(before['sinks']),before=before,after=after,
        scope='Resolved inverter parity at every non-inverter native input plus unchanged LUT INIT and register subtype/clock polarity. Caller must additionally check all physical state and full STA.')
