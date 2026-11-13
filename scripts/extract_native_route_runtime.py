"""Read native device tables and query intrinsic wire endpoints, without P&R.

All native calls are restricted to argument parsing, device loading and
read-only architecture queries. A hash guard binds the ABI to the audited DLL.
"""
import argparse,json,os,re,struct,subprocess,sys,time
from pathlib import Path
from gowin_probe import ROOT,BIN,prepare
from run_abc_reproduction import sha
from capture_runtime_tables import EXPECTED

def worker(out,full):
    import ctypes as C
    C.windll.kernel32.SetErrorMode(0x8003)
    handles=[os.add_dll_directory(str(p)) for p in (Path(sys.base_prefix)/'DLLs',Path(sys.base_prefix)/'Library/bin',BIN) if p.exists()]
    if sha(BIN/'gowin.dll')!=EXPECTED:raise ValueError('Unreviewed DLL build')
    os.chdir(out);dll=C.CDLL(str(BIN/'gowin.dll'))
    def fn(name,result,args):
        f=getattr(dll,name);f.restype=result;f.argtypes=args;return f
    vp=C.c_void_p;sp=C.POINTER(C.c_short)
    parse=fn('?readInUserCommand@@YAHHPEAPEAD_N@Z',C.c_int,[C.c_int,C.POINTER(C.c_char_p),C.c_bool])
    argv=[str(BIN/'gowin.exe'),'-do',str(out/'cmd.do')];av=(C.c_char_p*len(argv))(*(x.encode() for x in argv))
    if parse(len(argv),av,True):raise ValueError('Native argument parser failed')
    mem=fn('?getGlobalMemPtr@@YAPEAVglobalMem@@XZ',vp,[])()
    load=fn('?readDevice@NETLIST@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])
    rc=load(mem);print('Native device load',rc,flush=True)
    if rc:raise ValueError('Native device loading failed')
    table=fn('?getRouteNodeTable@globalMem@@QEAAPEAVrouteNodeTable@TEMPLATE@@XZ',vp,[vp])(mem)
    chip=fn('?getDevChip@globalMem@@QEAAPEAVchip@NETLIST@@XZ',vp,[vp])(mem)
    if not table or not chip:raise ValueError('Uninitialized native architecture tables')
    rows=C.c_short.from_address(chip+0x28).value;cols=C.c_short.from_address(chip+0x2a).value
    print('Native dimensions',rows,cols,flush=True)
    # Native chip includes one sentinel border on each side; FSE is 109x182.
    if (rows,cols)!=(111,184):raise ValueError('Unexpected native device dimensions')
    getgrid=fn('?getGridByRowCol@chip@NETLIST@@QEAAPEAVgrid@2@FF@Z',vp,[vp,C.c_short,C.c_short])
    grid=[];classes={}
    for r in range(rows):
        line=[]
        for c in range(cols):
            gp=getgrid(chip,r,c)
            if not gp:line.append(None);continue
            kind=C.c_int.from_address(gp+0x18).value;line.append(kind)
            if kind not in classes:
                vtable=C.c_void_p.from_address(gp).value;name=None;base=dll._handle
                if vtable and base<=vtable<base+0x2000000:
                    locator=C.c_void_p.from_address(vtable-8).value
                    if locator and base<=locator<base+0x2000000:
                        descriptor=C.c_uint32.from_address(locator+12).value
                        if descriptor<0x2000000:name=C.string_at(base+descriptor+16,128).split(b'\0')[0].decode('ascii',errors='replace')
                classes[kind]=dict(class_name=name,vtable_rva=hex(vtable-base) if vtable else None)
        grid.append(line)
    (out/'native_grid_types.json').write_text(json.dumps(dict(grid=grid,classes=classes),indent=2))
    getblk=fn('?get_blk@iob@NETLIST@@QEAAPEAVioblk@2@F@Z',vp,[vp,C.c_short])
    getins=fn('?getInNode@iobuf@NETLIST@@QEAAPEAVnode@2@XZ',vp,[vp])
    getouts=fn('?getOutNode@iobuf@NETLIST@@QEAAPEAVnode@2@XZ',vp,[vp])
    getoe=fn('?getOeNode@iobuf@NETLIST@@QEBAPEAVnode@2@XZ',vp,[vp])
    iob_rows=[]
    for r,row in enumerate(grid):
        for c,kind in enumerate(row):
            if kind!=1:continue
            gp=getgrid(chip,r,c)
            for slot in (0,1):
                block=getblk(gp,slot)
                if not block:continue
                begin=C.c_void_p.from_address(block+0x10).value;end=C.c_void_p.from_address(block+0x18).value
                count_buf=(end-begin)//8 if begin and end else 0
                if not 0<=count_buf<=100:raise ValueError('Unexpected I/O buffer vector')
                buffers=[]
                for j in range(count_buf):
                    buf=C.c_void_p.from_address(begin+8*j).value;ports={}
                    for name,get in [('in',getins),('out',getouts),('oe',getoe)]:
                        node=get(buf)
                        ports[name]=list(struct.unpack('<3h',C.string_at(node,6))) if node else None
                    buffers.append(ports)
                iob_rows.append(dict(native_row=r,native_column=c,slot=slot,buffers=buffers))
    (out/'native_iob_ports.json').write_text(json.dumps(iob_rows,indent=2))
    count=C.c_ushort.from_address(table+0x341a).value
    if not 1<=count<=4096:raise ValueError('Invalid native node count')
    names={}
    for i in range(count):
        p=table+0x18+i*32 if i<320 else C.c_void_p.from_address(table+0x3220).value+(i-320)*32
        size=C.c_uint64.from_address(p+16).value;capacity=C.c_uint64.from_address(p+24).value
        if size>100:raise ValueError('Unexpected native string size')
        if capacity>=16:p=C.c_void_p.from_address(p).value
        names[i]=C.string_at(p,size).decode('ascii')
    # Read-only introspection of native CFU primitive arrays. Guard all
    # candidate pointers through ReadProcessMemory, never assume a layout
    # learned from a different family or an open database.
    rpm=C.windll.kernel32.ReadProcessMemory;rpm.argtypes=[vp,vp,vp,C.c_size_t,C.POINTER(C.c_size_t)];rpm.restype=C.c_int
    def memory(address,size):
        if not address or address<65536:return None
        b=C.create_string_buffer(size);got=C.c_size_t()
        if not rpm(vp(-1),vp(address),b,size,C.byref(got)) or got.value!=size:return None
        return b.raw
    def qword(address):
        b=memory(address,8);return struct.unpack('<Q',b)[0] if b else None
    def class_name(address):
        v=qword(address);base=dll._handle
        if not v or not base<=v<base+0x2000000:return None
        loc=qword(v-8);b=memory(loc,24)
        if not b:return None
        td=struct.unpack_from('<I',b,12)[0]
        if td>=0x2000000:return None
        s=memory(base+td+16,160)
        return s.split(b'\0')[0].decode('ascii',errors='replace') if s else None
    sample_site=next((r,c) for r,row in enumerate(grid) for c,k in enumerate(row) if k==5 and r>10 and c>10)
    gp=getgrid(chip,*sample_site);arrays=[]
    # CFU allocation is 0x90 bytes in this guarded build. Earlier exploratory
    # captures scanned into adjacent CFUs; those are not field evidence.
    for offset in range(0x20,0x80,8):
        array=qword(gp+offset);entries=[]
        direct=class_name(array)
        if direct and any(s in direct.lower() for s in ['lut','reg','cls','alu']):
            raw=memory(array,512)
            arrays.append(dict(cfu_field_offset=hex(offset),direct_class_name=direct,raw_object_region=raw.hex() if raw else None,entries=[]))
        # Native encoder accesses the CFU +0x70 aggregate through +0x380;
        # its first eight pointers are LUTs, not the whole allocation.
        for i in range(0x380//8 if offset==0x70 else 8):
            obj=qword(array+i*8) if array else None;typ=class_name(obj)
            if not typ or not any(s in typ.lower() for s in ['lut','reg','cls','alu']):continue
            ports=[]
            for port_offset in range(0x18,0x70,8):
                node=qword(obj+port_offset);b=memory(node,6)
                if not b:continue
                rr,cc,index=struct.unpack('<3h',b)
                if 0<=rr<rows and 0<=cc<cols and index in names:ports.append(dict(object_offset=hex(port_offset),native_node=[rr,cc,index],node_name=names[index]))
            children=[]
            for child_offset in range(0x18,0x120,8):
                ptr=qword(obj+child_offset);child=class_name(ptr);embedded=class_name(obj+child_offset)
                if child and 'reg@' in child.lower():children.append(dict(offset=hex(child_offset),indirect=True,class_name=child,header=memory(ptr,64).hex()))
                if embedded and 'reg@' in embedded.lower():children.append(dict(offset=hex(child_offset),indirect=False,class_name=embedded,header=memory(obj+child_offset,64).hex()))
            vt=qword(obj);vtable=[hex(qword(vt+8*j)-dll._handle) if qword(vt+8*j) and dll._handle<=qword(vt+8*j)<dll._handle+0x2000000 else None for j in range(16)]
            entries.append(dict(index=i,class_name=typ,primitive_header=memory(obj,24).hex(),candidate_node_fields=ports,register_children=children,vtable_rvas=vtable))
        if entries:arrays.append(dict(cfu_field_offset=hex(offset),entries=entries))
    (out/'native_cfu_layout.json').write_text(json.dumps(dict(site=sample_site,cfu_header=memory(gp,0x90).hex(),arrays=arrays,scope='Native CFU memory/layout evidence; candidate field roles require code cross-check.'),indent=2))
    # The CFU aggregate has LUT pointers at +0 and register pointers at +0xa0.
    # Native Reg::setPin2Node establishes the five register node fields.
    # Validate every resource instead of extending a sample's control grouping.
    register_templates={};register_count=0;cfu_count=0
    for r,row in enumerate(grid):
        for c,kind in enumerate(row):
            if kind!=5:continue
            cfu_count+=1;aggregate=qword(getgrid(chip,r,c)+0x70)
            if not aggregate:raise ValueError('CFU aggregate missing')
            for slot in range(8):
                reg=qword(aggregate+0xa0+8*slot)
                if class_name(reg)!='.?AVreg@NETLIST@@':raise ValueError('Unrecognized physical register class')
                header=struct.unpack('<4H',memory(reg+0x10,8))
                if header!=(50,r,c,slot):raise ValueError('Register position does not match CFU')
                ports={}
                for pin,offset in [('D',0x18),('CLK',0x20),('RESET',0x28),('CE',0x30),('Q',0x38)]:
                    node=qword(reg+offset);raw=memory(node,6)
                    if not raw:raise ValueError('Register node missing')
                    rr,cc,ii=struct.unpack('<3h',raw)
                    if not 0<=rr<rows or not 0<=cc<cols or ii not in names:raise ValueError('Invalid register node')
                    ports[pin]=dict(name=names[ii],offset=[rr-r,cc-c])
                signature=json.dumps([slot,ports],sort_keys=True)
                if signature not in register_templates:register_templates[signature]=dict(slot=slot,ports=ports,site_count=0,example_native_site=[r,c])
                register_templates[signature]['site_count']+=1;register_count+=1
    (out/'native_register_ports.json').write_text(json.dumps(dict(cfu_count=cfu_count,register_count=register_count,
        templates=list(register_templates.values()),scope='All physical FF node bindings after native device initialization. Does not establish input/output mux modes or configuration encodings.'),indent=2))
    print('Validated native register bindings',register_count,flush=True)
    clock_names={}
    for label,offset in [('cmux_names',0x3230),('cmux_aux_names',0x3248)]:
        begin=C.c_void_p.from_address(table+offset).value;end=C.c_void_p.from_address(table+offset+8).value
        length=(end-begin)//32 if begin and end else 0
        if not 0<=length<=10000:raise ValueError('Invalid clock name vector')
        values=[]
        for i in range(length):
            p=begin+i*32;size=C.c_uint64.from_address(p+16).value;cap=C.c_uint64.from_address(p+24).value
            if size>200:raise ValueError('Invalid native clock string')
            if cap>=16:p=C.c_void_p.from_address(p).value
            values.append(C.string_at(p,size).decode('ascii'))
        clock_names[label]=values
    (out/'native_clock_names.json').write_text(json.dumps(clock_names,indent=2))
    funcs={}
    for length,arity,suffix in [(1,3,'FFFAEAF00'),(2,6,'FFFAEAF00000'),(8,6,'FFFAEAF00000')]:
        name=f'?getX{length}wireInf@routeNodeTable@TEMPLATE@@QEAAX{suffix}@Z'
        funcs[length]=fn(name,None,[vp,C.c_short,C.c_short,C.c_short]+[sp]*arity)
    switch=C.c_void_p.from_address(mem+0x30).value
    switch_storage=None
    if not switch:
        # The constructor sets the CIU connection stride to 60. Zeroed
        # storage alone corrupts CIU tables even when ordinary fanouts work.
        switch_storage=C.create_string_buffer(0x78);switch=C.addressof(switch_storage)
        fn('??0switchTable@TEMPLATE@@QEAA@XZ',vp,[vp])(switch)
        fn('?setSwitchTable@globalMem@@QEAAXPEAVswitchTable@TEMPLATE@@@Z',None,[vp,vp])(mem,switch)
        fn('?buildSwitchTable@switchTable@TEMPLATE@@QEAAXPEAVglobalMem@@@Z',None,[vp,vp])(switch,mem)
    print('Switch state',bool(switch),[bool(C.c_void_p.from_address(switch+o).value) for o in (0x38,0x40)] if switch else [],flush=True)
    # DAT-derived native switch connectivity is a separate namespace oracle
    # for validating FSE IDs, not assumed equal to runtime node indices.
    fan_counts=C.c_void_p.from_address(table+0x3328).value
    fan_rows=C.c_void_p.from_address(table+0x33d0).value
    fanout=[]
    if not fan_counts or not fan_rows:raise ValueError('Native fanout tables absent')
    for i in range(count):
        amount=C.c_ushort.from_address(fan_counts+2*i).value
        if amount>1024:raise ValueError('Unexpected native fanout count')
        pointer=C.c_void_p.from_address(fan_rows+8*i).value;dest=[]
        if amount and not pointer:raise ValueError('Native fanout count without storage')
        for j in range(amount):
            node=C.c_void_p.from_address(pointer+8*j).value
            if not node:raise ValueError('Empty native fanout entry')
            dest.append(list(struct.unpack('<3h',C.string_at(node,6))))
        fanout.append(dict(source_id=i,source_name=names[i],destinations=dest))
    (out/'native_fanouts.json').write_text(json.dumps(fanout,indent=2))
    # Endpoint-query direction is established by calling the native routines
    # for every index; do not assume names ending in 0 are accepted inputs.
    roots=[(index,length) for index in names for length in (1,2,8)]
    sample=[];records=0;queries=0;start=time.monotonic()
    positions=((r,c) for r in range(1,rows-1) for c in range(1,cols-1)) if full else iter([(50,90),(1,1),(rows-2,cols-2),(0,0)])
    with (out/'intrinsic_endpoints.bin').open('wb') as stream:
        for r,c in positions:
            for index,length in roots:
                arity=3 if length==1 else 6;outputs=[C.c_short(-32768) for _ in range(arity)]
                funcs[length](table,r,c,index,*[C.byref(v) for v in outputs]);queries+=1
                values=[v.value for v in outputs]
                for j in range(0,arity,3):
                    rr,cc,ii=values[j:j+3]
                    if -32768 in (rr,cc,ii):continue
                    if not (0<=rr<rows and 0<=cc<cols and ii in names):raise ValueError('Invalid intrinsic wire endpoint')
                    stream.write(struct.pack('<6h',r,c,index,rr,cc,ii));records+=1
                    if len(sample)<20:sample.append(dict(source=[r,c,names[index]],destination=[rr,cc,names[ii]]))
    source=BIN.parent/'share/device/GW5AST-138C'
    result=dict(status='extracted' if records else 'incomplete_no_endpoints',scope='Native device/table initialization and read-only intrinsic X1/X2/X8 endpoint queries; not a completed P&R.',
        open_chip_database_used=False,placement_executed=False,routing_executed=False,dll_sha256=EXPECTED,rows=rows,columns=cols,
        nodes=names,root_nodes=len(roots),queries=queries,records=records,full_grid=full,query_seconds=time.monotonic()-start,
        native_to_fse_coordinate_offset=[-1,-1],grid_sha256=sha(out/'native_grid_types.json'),
        iob_ports_sha256=sha(out/'native_iob_ports.json'),
        clock_names_sha256=sha(out/'native_clock_names.json'),
        cfu_layout_sha256=sha(out/'native_cfu_layout.json'),
        register_ports_sha256=sha(out/'native_register_ports.json'),
        fanouts_sha256=sha(out/'native_fanouts.json'),
        record_format='<6h: source_row,source_col,source_node_id,destination_row,destination_col,destination_node_id',
        endpoints_sha256=sha(out/'intrinsic_endpoints.bin'),sample=sample,sources={p.name:sha(p) for p in source.iterdir() if p.suffix in ('.dat','.fse','.tm')})
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print({k:v for k,v in result.items() if k not in ('nodes','sample')},flush=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--worker',action='store_true');ap.add_argument('--full',action='store_true');a=ap.parse_args();out=a.out.resolve()
    if a.worker:return worker(out,a.full)
    prepare(out);(out/'extractor_source.py.txt').write_bytes(Path(__file__).read_bytes());python=ROOT/'binary_audit/flow/runtime/bin/python.exe'
    command=[str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)]+(['--full'] if a.full else [])
    with (out/'worker.log').open('w') as log:
        cp=subprocess.run(command,cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=300)
    if cp.returncode:
        (out/'manifest.json').write_text(json.dumps(dict(status='failed',returncode=cp.returncode,open_chip_database_used=False),indent=2))
        raise RuntimeError('Native architecture extraction failed; inspect worker.log')
if __name__=='__main__':main()
