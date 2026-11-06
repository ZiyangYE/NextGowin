"""Query complete wide trees feeding FF slots; no P&R or bitstream claim."""
import argparse,json,os,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import sha
from workspace_budget import reserve
from ownpr_mux_architecture import children


def fixture(reset_signal=False,ce_signal=False):
    lines=['module top(clk,a,q); input clk; input [6:0] a; output [8:0] q; wire ck,one,zero,lp_o; wire [6:0] x;',
           'IBUF bc(.I(clk),.O(ck)); VCC vc(.V(one)); GND gc(.G(zero)); GSR gs(.GSRI(one));']
    for i in range(7):lines.append(f'IBUF b{i}(.I(a[{i}]),.O(x[{i}]));')
    specs={};outputs={}
    for width,root_slot in ((5,0),(6,1),(7,3)):
        entries=[]
        def node(level,slot):
            name=f'w{width}_{level}_{slot}';wire=name+'_o';lines.append('wire '+wire+';')
            if level==4:lines.extend([f'LUT4 {name}(.I0(x[0]),.I1(x[1]),.I2(x[2]),.I3(x[3]),.F({wire}));',f"defparam {name}.INIT=16'hA639;"])
            else:
                ins=[node(int(t[-1]),s) for t,s in children(level,slot)]
                lines.append(f'MUX2_LUT{level} {name}(.I0({ins[0]}),.I1({ins[1]}),.S0(x[{level-1}]),.O({wire}));')
            entries.append(dict(name=name,level=level,slot=slot));return wire
        output=node(width,root_slot);specs[str(width)]=entries;outputs[str(width)]=output;lines.append(f'OBUF o{width}(.I({output}),.O(q[{width-5}]));')
    lines.extend(["LUT4 lp(.I0(x[0]),.I1(zero),.I2(zero),.I3(zero),.F(lp_o)); defparam lp.INIT=16'hAAAA;",'OBUF ol(.I(lp_o),.O(q[3]));'])
    for i,style in enumerate(('external','paired','5','6','7'),4):
        data={'external':'x[0]','paired':'lp_o',**outputs}[style]
        reset='x[6]' if reset_signal else 'zero'
        ce='x[5]' if ce_signal else 'one'
        lines.extend([f'wire rq_{style};',f'DFFRE reg_{style}(.D({data}),.CLK(ck),.CE({ce}),.RESET({reset}),.Q(rq_{style}));',f'OBUF of_{style}(.I(rq_{style}),.O(q[{i}]));'])
    lines.append('endmodule')
    return '\n'.join(lines)+'\n',specs


def worker(out,encode_links=False):
    from native_device_session import NativeDeviceSession
    n=NativeDeviceSession(out)
    import ctypes as C
    from ownpr_device import Device
    dev=Device();vp=C.c_void_p;ptr=n.ptr;fn=n.fn;specs=json.loads((out/'specs.json').read_text())
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(n.mem);vec=(C.c_uint64*3)()
    fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vec))
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    objects={}
    for a in range(vec[0],vec[1],8):
        obj=ptr(a);buf=C.create_string_buffer(32);getname(obj,C.addressof(buf));b=C.addressof(buf);length=ptr(b+16)
        if length>128:raise ValueError('Unexpected fixture instance name')
        objects[C.string_at(ptr(b) if ptr(b+24)>=16 else b,length).decode()]=obj
    setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp]);setlogic=fn('?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp]);getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])
    bindlut=fn('?setPin2Node@Lut4@NETLIST@@UEAAXXZ',None,[vp]);bindmux=fn('?setPin2Node@Mux2@NETLIST@@UEAAXXZ',None,[vp]);bindreg=fn('?setPin2Node@Reg@NETLIST@@UEAAXXZ',None,[vp])
    getreg=fn('?getOnePin@Reg@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint]);getlut=fn('?getOnePin@Lut4@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint])
    direct=fn('?isDirectConn@Pin@NETLIST@@QEAA_NPEAVglobalMem@@@Z',C.c_bool,[vp,vp]);route=fn('?getNodebyPin@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z',C.c_bool,[vp,vp,vp])
    storage=C.create_string_buffer(0x188);encoder=C.CFUNCTYPE(vp,vp,vp,C.c_bool)(n.dll._handle+0x4f8d40)(C.addressof(storage),n.mem,False)
    if encoder!=C.addressof(storage):raise ValueError('Native encoder constructor mismatch')
    convert=C.CFUNCTYPE(None,vp,vp)(n.dll._handle+0x558700)
    rows=[];encoding_records=[];reset_signal='.RESET(x[6])' in (out/'probe.vg').read_text();ce_signal='.CE(x[5])' in (out/'probe.vg').read_text()
    for r,c in ((50,90),(101,164)):
        agg=ptr(n.getgrid(n.chip,r,c)+0x70)
        for style in ('external','paired','5','6','7'):
            if encode_links and style not in specs:continue
            reg_name='reg_'+style;reg=objects[reg_name];data=getreg(reg,0);original_net=ptr(data+8)
            for ff_slot in range(8):
                if encode_links and ff_slot!={'5':0,'6':1,'7':3}[style]:continue
                saved=[];bindings=[]
                def save(address,count):saved.append((address,C.string_at(address,count)))
                def bind(name,physical,is_mux=False):
                    obj=objects[name];bindings.append((obj,getphy(obj)));save(physical+8,8)
                    if is_mux:save(physical+0x18,8)
                    setphy(obj,physical);setlogic(physical,obj)
                    if is_mux:C.c_uint64.from_address(physical+0x18).value=obj
                    return obj
                leaf_slots=set();mux_slots=set();net=original_net;output_pin=None;groups=[ptr(agg+0x360+8*i) for i in range(4)]
                try:
                    save(agg+0x318,8);save(agg+0x326,8)
                    for group in groups:save(group+6,38)
                    for slot in range(8):
                        lut=ptr(agg+8*slot);save(lut+0x20,3);C.c_ubyte.from_address(lut+0x22).value=1
                    entries=specs[style] if style in specs else ([dict(name='lp',level=4,slot=ff_slot)] if style=='paired' else [])
                    for row in entries:
                        slot=row['slot'];is_mux=row['level']>4;physical=ptr(agg+(0x150 if is_mux else 0)+8*slot);obj=bind(row['name'],physical,is_mux)
                        if is_mux:mux_slots.add(slot);bindmux(obj)
                        else:
                            leaf_slots.add(slot);C.c_ushort.from_address(physical+0x20).value=0xAAAA if style=='paired' else 0xA639;C.c_ubyte.from_address(physical+0x22).value=0;C.c_ubyte.from_address(agg+0x326+slot).value=1;bindlut(obj)
                    if style=='paired':output_pin=getlut(objects['lp'],8);net=ptr(output_pin+8)
                    elif style in specs:
                        output=(C.c_uint64*3)();fn('?getAllOutPins@Mux2@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(objects[entries[-1]['name']],C.addressof(output))
                        if output[1]-output[0]!=8:raise ValueError('Ambiguous wide root output')
                        output_pin=ptr(output[0]);net=ptr(output_pin+8)
                    bind(reg_name,ptr(agg+0xa0+8*ff_slot));C.c_ubyte.from_address(agg+0x318+ff_slot).value=1
                    if ptr(data+8)!=net:raise ValueError('Fixture logical FF connection differs from driver')
                    bindreg(reg)
                    convert(encoder,n.getgrid(n.chip,r,c))
                    is_direct=direct(data,n.mem);vector=(C.c_uint64*3)();ok=route(data,C.addressof(vector),n.mem);count=(vector[1]-vector[0])//8 if vector[0] else 0
                    if not 0<=count<=8:raise ValueError('Unexpected data routing endpoint count')
                    points=[list(struct.unpack('<3h',C.string_at(ptr(a),6))) for a in range(vector[0],vector[1],8)] if count else []
                    source_points=[]
                    if output_pin:
                        sources=(C.c_uint64*3)();route(output_pin,C.addressof(sources),n.mem)
                        source_points=[list(struct.unpack('<3h',C.string_at(ptr(a),6))) for a in range(sources[0],sources[1],8)] if sources[0] else []
                    leaf_points={4*s+i for s in leaf_slots for i in range(4)};selector_points={133+s for s in mux_slots}
                    rows.append(dict(site=[r,c],tile_type=dev.grid[r-1][c-1],driver=style,ff_slot=ff_slot,direct=is_direct,route_query_return=ok,data_points=points,data_names=[dev.names.get(p[2]) for p in points],
                        ff_profile=dict(cell_type='DFFRE',ce='signal' if ce_signal else 'one',control='signal' if reset_signal else 'zero',clock='signal'),
                        source_data_points=source_points,source_data_names=[dev.names.get(p[2]) for p in source_points],source_direct_fanouts_to_data=[dict(source=p,destination=q,edge_present=tuple(q) in n.fanouts(tuple(p))) for p in source_points for q in points],ff_shares_active_leaf=ff_slot in leaf_slots,leaf_input_conflict=any(p[2] in leaf_points for p in points),selector_input_conflict=any(p[2] in selector_points for p in points),
                        group_attributes=[list(struct.unpack('<19h',C.string_at(g+6,38))) for g in groups],
                        scope='Binding and native attribute-conversion query; not bit encoding or placement legality proof'))
                    if encode_links:
                        from native_wide_ff_encoding import capture
                        encoding_records.append(capture(n,out,dev,rows[-1],groups,entries))
                        (out/'encoding.json').write_text(json.dumps(dict(records=encoding_records,production_enabled=False),indent=2))
                finally:
                    C.c_uint64.from_address(data+8).value=original_net
                    for obj,previous in reversed(bindings):setphy(obj,previous)
                    for address,raw in reversed(saved):C.memmove(address,raw,len(raw))
                    if any(C.string_at(a,len(raw))!=raw for a,raw in saved):raise ValueError('Native binding restoration mismatch')
    (out/'routes.json').write_text(json.dumps(dict(records=rows,saved_physical_fields_restored=True,logical_pin_cache_restoration_verified=False,production_enabled=False),indent=2));print('queries',len(rows))
    if encode_links:
        from ownpr_clock_images import compact
        compact(out)


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');p.add_argument('--encode-links',action='store_true');p.add_argument('--ff-reset-signal',action='store_true');p.add_argument('--ff-ce-signal',action='store_true');a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out,a.encode_links)
    from gowin_probe import prepare
    from ownpr_device import DLL_SHA
    budget=reserve(ROOT,350000000 if a.encode_links else 30000000);prepare(out);vg,specs=fixture(a.ff_reset_signal,a.ff_ce_signal);(out/'probe.vg').write_text(vg);(out/'specs.json').write_text(json.dumps(specs));shutil.copyfile(__file__,out/'source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic()
    if a.encode_links:
        cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+'-bit\n');shutil.copyfile(Path(__file__).with_name('native_wide_ff_encoding.py'),out/'encoding_source.py.txt')
    command=[str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)]+(['--encode-links'] if a.encode_links else [])
    rc=None;expired=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run(command,cwd=ROOT,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=600 if a.encode_links else 120).returncode
        except subprocess.TimeoutExpired:expired=True
    m=dict(status='captured' if rc==0 else 'failed',returncode=rc,timeout=expired,wall_seconds=time.monotonic()-start,dll_sha256=DLL_SHA,vendor_pnr_executed=False,device_programmed=False,source_sha256=sha(out/'probe.vg'),storage_reservation=budget)
    if rc==0:m['routes_sha256']=sha(out/'routes.json')
    if (out/'encoding.json').exists():m['encoding_sha256']=sha(out/'encoding.json')
    (out/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps(m,indent=2))
    if rc!=0:raise SystemExit(1)


if __name__=='__main__':main()
