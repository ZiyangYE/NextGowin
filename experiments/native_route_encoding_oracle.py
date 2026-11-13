"""Inspect native route-to-configuration objects without running P&R."""
import argparse,json,os,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import BIN,prepare
from capture_runtime_tables import EXPECTED
from run_abc_reproduction import sha


def worker(out,pip_sweep=False,native_flow_topology=False,all_normal=False,hard_defaults=False):
    handles=[os.add_dll_directory(str(p)) for p in (Path(sys.base_prefix)/'DLLs',Path(sys.base_prefix)/'Library/bin',BIN)]
    import ctypes as C
    C.windll.kernel32.SetErrorMode(0x8003)
    if sha(BIN/'gowin.dll')!=EXPECTED:raise ValueError('Unreviewed native ABI')
    os.chdir(out);dll=C.CDLL(str(BIN/'gowin.dll'));vp=C.c_void_p
    def fn(name,result,args):
        f=getattr(dll,name);f.restype=result;f.argtypes=args;return f
    def ptr(address):return C.c_uint64.from_address(address).value
    def cls(obj):
        vt=ptr(obj)
        if not dll._handle<=vt<dll._handle+0x2000000:return None
        loc=ptr(vt-8);td=C.c_uint32.from_address(loc+12).value
        if td>=0x2000000:raise ValueError('Invalid native RTTI')
        return C.string_at(dll._handle+td+16).decode('ascii')
    args=[str(BIN/'gowin.exe'),'-do',str(out/'cmd.do')];av=(C.c_char_p*len(args))(*(s.encode() for s in args))
    if fn('?readInUserCommand@@YAHHPEAPEAD_N@Z',C.c_int,[C.c_int,C.POINTER(C.c_char_p),C.c_bool])(len(args),av,True):raise ValueError('Parser failed')
    if native_flow_topology:
        if fn('?executePnrTask@@YAHXZ',C.c_int,[])():raise ValueError('Fixed-pin native topology calibration failed')
    mem=fn('?getGlobalMemPtr@@YAPEAVglobalMem@@XZ',vp,[])()
    if not native_flow_topology:
        for name in ('?readDevice@NETLIST@@YAHPEAVglobalMem@@@Z','?readConfigAttFile@@YAHPEAVglobalMem@@@Z','?readInput@VLGPARSE@@YAHPEAVglobalMem@@@Z'):
            if fn(name,C.c_int,[vp])(mem):raise ValueError('Native initialization failed')
    constructor=C.CFUNCTYPE(vp,vp,vp,C.c_bool)(dll._handle+0x4f8d40)
    storage=C.create_string_buffer(0x188);encoder=constructor(C.addressof(storage),mem,False)
    if encoder!=C.addressof(storage):raise ValueError('Native encoder constructor mismatch')
    filename=C.create_string_buffer(32)
    C.CFUNCTYPE(vp,vp,C.c_int,vp)(dll._handle+0x4f5ae0)(C.addressof(filename),70,mem)
    address=C.addressof(filename);length=C.c_uint64.from_address(address+16).value;capacity=C.c_uint64.from_address(address+24).value
    path=C.string_at(ptr(address) if capacity>=16 else address,length).decode()
    print('NATIVE_FSE_PATH',path,flush=True)
    if not C.CFUNCTYPE(C.c_bool,vp,vp)(dll._handle+0x53a8c0)(encoder,C.addressof(filename)):
        raise ValueError('Native FSE parser failed')
    begin,end=ptr(encoder),ptr(encoder+8);count=(end-begin)//8
    if not 151<=count<=4096:raise ValueError('Unexpected native FSE block array')
    blocks=[]
    for index in (17,150,221,228):
        block=ptr(begin+8*index)
        if not block:raise ValueError('Missing native FSE block')
        tb,te=ptr(block+0x18),ptr(block+0x20);tables=[]
        if te<tb or (te-tb)%8 or (te-tb)//8>256:raise ValueError('Bad native table vector')
        for offset in range(tb,te,8):
            table=ptr(offset);typ=C.c_int.from_address(table+8).value
            # The BFD-array subclass has a vector here, not a scalar row count.
            rows=None if typ==61 else C.c_int.from_address(table+0x10).value;data=ptr(table+0x18)
            tables.append(dict(type=typ,class_name=cls(table),rows=rows,first_record_bytes=C.string_at(data,min(32,rows*6)).hex() if data and rows else None))
        blocks.append(dict(index=index,class_name=cls(block),tables=tables))
    (out/'native_fse_objects.json').write_text(json.dumps(dict(block_count=count,path=path,blocks=blocks),indent=2))
    print('NATIVE_FSE_OBJECTS',[(b['index'],[(t['type'],t['rows']) for t in b['tables']]) for b in blocks],flush=True)
    if pip_sweep:
        import numpy as np
        from ownpr_device import Device
        device=Device();requests=set();implicit=set();topology={}
        if hard_defaults:
            # Reuse the retained, hash-checked implicit capture. The older
            # large fanout cache was removed during authorized disk cleanup.
            retained=device.implicit_connections();fanout_path=ROOT/'results/native_pip_encoding_011/native_pip_writes.json'
            native_pairs=set()
        else:
            runtime=ROOT/'results/native_route_runtime_019';runtime_meta=json.loads((runtime/'manifest.json').read_text())
            fanout_path=runtime/'native_fanouts.json'
            if runtime_meta.get('dll_sha256')!=EXPECTED or runtime_meta.get('open_chip_database_used') is not False or sha(fanout_path)!=runtime_meta.get('fanouts_sha256'):
                raise ValueError('Unverified native fanout template')
            fanouts=json.loads(fanout_path.read_text())
            native_pairs={(r['source_id'],q[2]) for r in fanouts for q in r['destinations'] if q[:2]==[-1,-1]}
        for tile in (17,221,222,223,224,225,226,227,228):
            literal={(r['source'],r['destination']) for r in device.connections(tile)}
            for row in device.connections(tile):
                if row['source_name'] is not None and row['destination_name'] is not None:requests.add((tile,row['source'],row['destination']))
            candidates={(r['source'],r['destination']) for r in retained[tile]} if hard_defaults else native_pairs-literal
            implicit.update((tile,s,d) for s,d in candidates)
        requests.update(implicit)
        if all_normal:
            for tile in {t for row in device.grid for t in row}:
                for row in device.connections(tile):
                    if row['source_name'] is not None and row['destination_name'] is not None:requests.add((tile,row['source'],row['destination']))
        chip=fn('?getDevChip@globalMem@@QEAAPEAVchip@NETLIST@@XZ',vp,[vp])(mem)
        getgrid=fn('?getGridByRowCol@chip@NETLIST@@QEAAPEAVgrid@2@FF@Z',vp,[vp,C.c_short,C.c_short])
        getnode=C.CFUNCTYPE(vp,vp,C.c_short)(dll._handle+0x20fcd0)
        getout=C.CFUNCTYPE(vp,vp,C.c_short)(dll._handle+0x211ed0)
        switch_storage=None
        if not ptr(mem+0x30):
            switch_storage=C.create_string_buffer(0x78);switch=C.addressof(switch_storage)
            fn('??0switchTable@TEMPLATE@@QEAA@XZ',vp,[vp])(switch)
            fn('?setSwitchTable@globalMem@@QEAAXPEAVswitchTable@TEMPLATE@@@Z',None,[vp,vp])(mem,switch)
            fn('?buildSwitchTable@switchTable@TEMPLATE@@QEAAXPEAVglobalMem@@@Z',None,[vp,vp])(switch,mem)
        # readDevice allocates nodes but leaves fanout degrees uninitialized.
        # This audited routine builds only logic/clock/local architecture
        # graphs and node flags; it runs no placement or routing optimizer.
        if native_flow_topology:
            if not C.c_ubyte.from_address(chip+8).value:raise ValueError('Native run did not initialize physical RRG')
        else:fn('?buildRRGraph@chip@NETLIST@@QEAAXXZ',None,[vp])(chip)
        print('NATIVE_RR_GRAPH_INITIALIZED',flush=True)
        samples={};all_samples={}
        for r,row in enumerate(device.grid):
            for c,tile in enumerate(row):
                if tile not in all_samples:all_samples[tile]=(r+1,c+1)
                if tile in (17,221,222,223,224,225,226,227,228) and tile not in samples:samples[tile]=(r+1,c+1)
        physical_sources={}
        for tile,source,dest in sorted(implicit):
            key=tile,source
            if key not in physical_sources:
                r,c=samples[tile];gp=getgrid(chip,r,c)
                if C.c_int.from_address(gp+0x18).value!=5:raise ValueError('Implicit-wire sample is not a native CFU')
                node=getnode(ptr(gp+0x10),source);amount=C.c_short.from_address(node+6).value
                if not 0<=amount<=4096:raise ValueError('Invalid native fanout count')
                destinations=set()
                for j in range(amount):
                    target=getout(node,j)
                    if target:destinations.add(struct.unpack('<3h',C.string_at(target,6)))
                physical_sources[key]=destinations
            r,c=samples[tile]
            topology[tile,source,dest]=(r,c,dest) in physical_sources[key]
        if not all(topology.values()):
            (out/'missing_implicit_topology.json').write_text(json.dumps([k for k,v in topology.items() if not v],indent=2))
            raise ValueError('Native physical fanout does not confirm all implicit candidates')
        fixed_pairs=[('SN10','S110'),('SN10','N110'),('SN20','S120'),('SN20','N120'),
                     ('EW10','E110'),('EW10','W110'),('EW20','E120'),('EW20','W120')]
        extra_fixed=0
        for tile,(r,c) in sorted(all_samples.items()):
            if tile in samples:continue
            gp=getgrid(chip,r,c)
            if not gp or not ptr(gp+0x10):continue
            node_table=ptr(gp+0x10);literal=device.connections(tile);destinations={q['destination'] for q in literal}
            source_cache={}
            for source_name,dest_name in fixed_pairs:
                source,dest=device.ids[source_name],device.ids[dest_name]
                if dest in destinations:continue
                if source not in source_cache:
                    node=getnode(node_table,source);destinations_native=set()
                    if node:
                        amount=C.c_short.from_address(node+6).value
                        if not 0<=amount<=4096:raise ValueError('Invalid boundary fanout count')
                        for j in range(amount):
                            target=getout(node,j)
                            if target:destinations_native.add(struct.unpack('<3h',C.string_at(target,6)))
                    source_cache[source]=destinations_native
                if (r,c,dest) in source_cache[source]:
                    key=tile,source,dest;implicit.add(key);requests.add(key);topology[key]=True;samples[tile]=(r,c);extra_fixed+=1
        hard_samples={}
        if hard_defaults:
            # Non-CFU output muxes also have absent, zero-selected FSE rows.
            # Discover actual Q0 fanouts in two locations per tile type; do
            # not infer these choices from CFU topology or a different tile.
            from collections import defaultdict
            locations=defaultdict(list)
            for r,row in enumerate(device.grid):
                for c,tile in enumerate(row):locations[tile].append((r+1,c+1))
            source=device.ids['Q0']
            for tile,sites in sorted(locations.items()):
                if tile in (17,221,222,223,224,225,226,227,228):continue
                observed=[];chosen=[sites[0],sites[-1]] if len(sites)>1 else [sites[0]]
                for r,c in chosen:
                    gp=getgrid(chip,r,c);node=getnode(ptr(gp+0x10),source);destinations=set()
                    if node:
                        degree=C.c_short.from_address(node+6).value
                        if not 0<=degree<=4096:raise ValueError('Invalid non-CFU output degree')
                        for index in range(degree):
                            target=getout(node,index)
                            if target:
                                rr,cc,d=struct.unpack('<3h',C.string_at(target,6))
                                if (rr,cc)==(r,c) and d in device.names:destinations.add(d)
                    observed.append(destinations)
                if any(x!=observed[0] for x in observed):raise ValueError('Non-CFU Q0 fanouts vary across same-type sites')
                literal={(r['source'],r['destination']) for r in device.connections(tile)}
                for dest in sorted(observed[0]):
                    key=tile,source,dest
                    if (source,dest) in literal or key in implicit:continue
                    implicit.add(key);requests.add(key);topology[key]=True;samples[tile]=chosen[0];hard_samples[key]=chosen
        route_file=ROOT/'results/ownpr_int2float_route_004/routes.json'
        used=[]
        routed_examples={} if hard_defaults else json.loads(route_file.read_text())['nets']
        for net in routed_examples.values():
            for r,c,source,dest in net['pips']:
                key=(device.grid[r][c],device.ids[source],device.ids[dest]);requests.add(key);used.append(key)
        emit=C.CFUNCTYPE(None,vp,vp,vp,C.c_short,C.c_short)(dll._handle+0x571180)
        records=[];last_tile=None
        for tile,source,dest in sorted(requests):
            if tile!=last_tile:
                block=ptr(begin+8*tile);height=C.c_int.from_address(block+8).value;width=C.c_int.from_address(block+12).value
                own_block=device.fse.blocks[str(tile)]
                if (height,width)!=(own_block['height'],own_block['width']):raise ValueError('Native bitmap dimensions mismatch')
                buffer=(C.c_ubyte*(height*width))();address=C.addressof(buffer);view=np.ctypeslib.as_array(buffer)
                row_vectors=(C.c_uint64*(height*3))()
                for r in range(height):row_vectors[3*r:3*r+3]=[address+r*width,address+(r+1)*width,address+(r+1)*width]
                rv=C.addressof(row_vectors);bitmap=(C.c_uint64*3)(rv,rv+height*24,rv+height*24);last_tile=tile
                print('PIP_TILE',tile,'COMPLETED_CHOICES',len(records),flush=True)
            C.memset(address,0,height*width);emit(encoder,C.addressof(bitmap),block,source,dest)
            ones=np.flatnonzero(view).tolist()
            if any(view[i]!=1 for i in ones):raise ValueError('Native encoder wrote a nonbinary bit')
            C.memset(address,1,height*width);emit(encoder,C.addressof(bitmap),block,source,dest)
            zeros=np.flatnonzero(view==0).tolist()
            is_implicit=(tile,source,dest) in implicit
            records.append(dict(tile_type=tile,source=source,destination=dest,set_bits=[list(divmod(i,width)) for i in ones],clear_bits=[list(divmod(i,width)) for i in zeros],
                implicit=is_implicit,physical_topology_verified=topology.get((tile,source,dest)),sample_native_site=samples.get(tile) if is_implicit else None,
                hard_default_sample_sites=hard_samples.get((tile,source,dest))))
        report=dict(records=records,requests=len(records),used_pips=used,used_routes_sha256=None if hard_defaults else sha(route_file),dll_sha256=EXPECTED,
            source_fse_sha256=device.fse.sha256,open_chip_database_used=False,vendor_placement_executed=native_flow_topology,vendor_routing_executed=native_flow_topology,
            implicit_connections=len(implicit),fixed_non_cfu_connections=extra_fixed,fanout_template_sha256=sha(fanout_path),
            scope='Native raw table-2/table-45 PIP write helper, zero and one bitmap probes. Does not yet validate high-level node remapping, default configuration, full routing or hardware.')
        (out/'native_pip_writes.json').write_text(json.dumps(report,separators=(',',':')));print('PIP_WRITES_CAPTURED',len(records),flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--worker',action='store_true');ap.add_argument('--pip-sweep',action='store_true');ap.add_argument('--native-flow-topology',action='store_true');ap.add_argument('--all-normal',action='store_true');ap.add_argument('--hard-defaults',action='store_true');a=ap.parse_args();out=a.out.resolve()
    if a.worker:return worker(out,a.pip_sweep,a.native_flow_topology,a.all_normal,a.hard_defaults)
    from workspace_budget import reserve
    reserve(ROOT,200_000_000)
    prepare(out);shutil.copyfile(__file__,out/'extractor_source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;timeout=False
    if a.native_flow_topology:
        for source,name in [(ROOT/'results/synthesis_wide_int157_001/lut4/05_netlist/mapped.vg','probe.vg'),
            (ROOT/'results/int2float_clock_ablation_001/lut4.cst','project.cst'),(ROOT/'results/fullio_closure_003/int_lut4_local155_edge.sdc','probe.sdc')]:shutil.copyfile(source,out/name)
        cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+f'-cst {(out/"project.cst").as_posix()}\n')
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)]+(['--pip-sweep'] if a.pip_sweep else [])+(['--native-flow-topology'] if a.native_flow_topology else [])+(['--all-normal'] if a.all_normal else [])+(['--hard-defaults'] if a.hard_defaults else []),cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=300).returncode
        except subprocess.TimeoutExpired:timeout=True
    result=dict(status='captured' if rc==0 and (out/'native_fse_objects.json').exists() else 'failed',returncode=rc,timeout=timeout,wall_seconds=time.monotonic()-start,
        dll_sha256=EXPECTED,open_chip_database_used=False,vendor_placement_executed=a.native_flow_topology,vendor_routing_executed=a.native_flow_topology,device_programmed=False,own_pnr_result=False)
    if a.native_flow_topology:result['fixed_cst_sha256']=sha(out/'project.cst');result['netlist_sha256']=sha(out/'probe.vg')
    if result['status']=='captured':result['objects_sha256']=sha(out/'native_fse_objects.json')
    if a.pip_sweep:
        if (out/'native_pip_writes.json').exists():result['pip_writes_sha256']=sha(out/'native_pip_writes.json')
        else:result['status']='failed'
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()
