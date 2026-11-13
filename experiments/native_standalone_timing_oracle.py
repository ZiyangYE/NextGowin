"""Initialize the native device and timing library without executing P&R.

Replay retained physical parent links solely to validate read-only wire timing
queries. Neither a vendor optimizer nor a board programmer is invoked.
"""
import argparse,json,os,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import BIN,prepare
from capture_runtime_tables import EXPECTED
from run_abc_reproduction import sha


def worker(out,reference):
    import ctypes as C
    C.windll.kernel32.SetErrorMode(0x8003)
    handles=[os.add_dll_directory(str(p)) for p in (Path(sys.base_prefix)/'DLLs',Path(sys.base_prefix)/'Library/bin',BIN)]
    if sha(BIN/'gowin.dll')!=EXPECTED:raise ValueError('Unreviewed native timing ABI')
    os.chdir(out);dll=C.CDLL(str(BIN/'gowin.dll'));vp=C.c_void_p
    def fn(name,result,args):
        f=getattr(dll,name);f.restype=result;f.argtypes=args;return f
    def ptr(address):return C.c_uint64.from_address(address).value
    args=[str(BIN/'gowin.exe'),'-do',str(out/'cmd.do')];av=(C.c_char_p*len(args))(*(s.encode() for s in args))
    if fn('?readInUserCommand@@YAHHPEAPEAD_N@Z',C.c_int,[C.c_int,C.POINTER(C.c_char_p),C.c_bool])(len(args),av,True):raise ValueError('Native command parsing failed')
    mem=fn('?getGlobalMemPtr@@YAPEAVglobalMem@@XZ',vp,[])()
    for name in ('?readDevice@NETLIST@@YAHPEAVglobalMem@@@Z','?readConfigAttFile@@YAHPEAVglobalMem@@@Z','?readInput@VLGPARSE@@YAHPEAVglobalMem@@@Z'):
        if fn(name,C.c_int,[vp])(mem):raise ValueError('Native initialization failed')
    switch_buffer=C.create_string_buffer(0x78);switch=C.addressof(switch_buffer)
    fn('??0switchTable@TEMPLATE@@QEAA@XZ',vp,[vp])(switch)
    fn('?setSwitchTable@globalMem@@QEAAXPEAVswitchTable@TEMPLATE@@@Z',None,[vp,vp])(mem,switch)
    fn('?buildSwitchTable@switchTable@TEMPLATE@@QEAAXPEAVglobalMem@@@Z',None,[vp,vp])(switch,mem)
    chip=fn('?getDevChip@globalMem@@QEAAPEAVchip@NETLIST@@XZ',vp,[vp])(mem)
    fn('?buildRRGraph@chip@NETLIST@@QEAAXXZ',None,[vp])(chip)
    print('STANDALONE_GRAPH_READY',flush=True)
    monitor=fn('?getTmMonitor@globalMem@@QEAAPEAVTmMonitor@STA@@XZ',vp,[vp])(mem)
    print('STANDALONE_MONITOR_CREATED',flush=True)
    # Same part-to-speed setup called by setTimingData, without its unrelated
    # constraint/analysis initialization. Avoid the monitor's default grade 5.
    C.CFUNCTYPE(None,vp)(dll._handle+0x13ac550)(mem)
    print('STANDALONE_SPEED_SELECTED',flush=True)
    context=ptr(monitor+8);speed_string=context+0x308;length=ptr(speed_string+16);capacity=ptr(speed_string+24)
    if length>15:raise ValueError('Unsupported speed string length')
    speed_bytes=C.string_at(ptr(speed_string) if capacity>=16 else speed_string,length)
    if speed_bytes!=b'C1/I0':raise ValueError('Unexpected part-derived speed')
    speed_argument=C.create_string_buffer(32);C.memmove(speed_argument,speed_bytes,length)
    C.c_uint64.from_address(C.addressof(speed_argument)+16).value=length
    C.c_uint64.from_address(C.addressof(speed_argument)+24).value=15
    fn('?setSpeedInfo@globalMem@@QEAAXV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])(mem,C.addressof(speed_argument))
    if not fn('?readTimingData@TmMonitor@STA@@QEAA_NXZ',C.c_bool,[vp])(monitor):raise ValueError('Native timing library load failed')
    context=ptr(monitor+8);library=ptr(context+0x38)
    if not library:raise ValueError('Native timing library absent')
    dimensions=struct.unpack('<3h',C.string_at(library+0x1c,6))
    if dimensions!=(4,9,13):raise ValueError('Native timing dimensions changed')
    speed_string=context+0x308;length=ptr(speed_string+16);capacity=ptr(speed_string+24)
    if length>64:raise ValueError('Invalid native speed string')
    speed=C.string_at(ptr(speed_string) if capacity>=16 else speed_string,length).decode('ascii')
    print('STANDALONE_TIMING_READY',dimensions,'SPEED',speed,flush=True)
    refmeta=json.loads((reference/'manifest.json').read_text());refpath=reference/'native_wire_delays.json'
    if refmeta.get('dll_sha256')!=EXPECTED or refmeta.get('open_chip_database_used') is not False or sha(refpath)!=refmeta.get('wire_delays_sha256'):
        raise ValueError('Reference capture provenance mismatch')
    data=json.loads(refpath.read_text());getgrid=fn('?getGridByRowCol@chip@NETLIST@@QEAAPEAVgrid@2@FF@Z',vp,[vp,C.c_short,C.c_short])
    getnode=C.CFUNCTYPE(vp,vp,C.c_short)(dll._handle+0x20fcd0)
    nodes={};flag_differences=[];saved=[]
    for row in data['nodes']:
        r,c,i=row['node'];grid=getgrid(chip,r,c);table=ptr(grid+0x10) if grid else None
        if not table or not 0<=i<C.c_short.from_address(table+8).value:raise ValueError('Reference node unavailable')
        node=getnode(table,i)
        if not node or list(struct.unpack('<3h',C.string_at(node,6)))!=row['node']:raise ValueError('Native physical node mismatch')
        nodes[r,c,i]=node
        before=C.string_at(node,0x30);reference_bytes=bytes.fromhex(row['native_node_bytes'])
        if before[0x2b]!=reference_bytes[0x2b]:flag_differences.append(dict(node=row['node'],initial=before[0x2b],reference=reference_bytes[0x2b]))
        saved.append((node,ptr(node+0x18),before[0x2a]))
    query=C.CFUNCTYPE(None,vp,vp,vp,C.c_int,C.c_int)(dll._handle+0x14733b0)
    records=[];mismatches=[]
    try:
        for row in data['nodes']:
            node=nodes[tuple(row['node'])];parent=row['parent']
            C.c_uint64.from_address(node+0x18).value=nodes[tuple(parent)] if parent else 0
            C.c_ubyte.from_address(node+0x2a).value|=8
        for row in data['nodes']:
            node=nodes[tuple(row['node'])];values=[]
            for selector in range(3):
                value=(C.c_float*4)();query(library,C.addressof(value),node,selector,70);values.append(list(value))
            records.append(dict(node=row['node'],values_by_selector=values))
            if values!=row['values_by_selector']:
                mismatches.append(dict(node=row['node'],parent=row['parent'],actual=values,expected=row['values_by_selector']))
    finally:
        for node,parent,flags in saved:
            C.c_uint64.from_address(node+0x18).value=parent;C.c_ubyte.from_address(node+0x2a).value=flags
    result=dict(status='matched_reference_queries' if not mismatches else 'query_mismatches',nodes=len(records),speed=speed,
        reference_sha256=sha(refpath),flag_differences=flag_differences,mismatch_count=len(mismatches),mismatches=mismatches,
        records=records,vendor_pnr_executed=False,own_pnr_result=False,
        scope='Device/timing initialization plus replay of captured parent links; not a placement, routing optimization or general timing proof.')
    (out/'standalone_timing.json').write_text(json.dumps(result,indent=2))
    print('STANDALONE_QUERY_RESULT',len(records),'MISMATCHES',len(mismatches),'FLAGS',len(flag_differences),flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--worker',action='store_true')
    ap.add_argument('--reference',type=Path,default=ROOT/'results/native_wire_delays_009');args=ap.parse_args()
    out=args.out.resolve();reference=args.reference.resolve()
    if args.worker:return worker(out,reference)
    prepare(out);shutil.copyfile(__file__,out/'extractor_source.py.txt')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;timeout=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out),'--reference',str(reference)],
            cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=120).returncode
        except subprocess.TimeoutExpired:timeout=True
    result=dict(status='captured' if rc==0 and (out/'standalone_timing.json').exists() else 'failed',returncode=rc,timeout=timeout,
        wall_seconds=time.monotonic()-start,dll_sha256=EXPECTED,open_chip_database_used=False,vendor_placement_executed=False,
        vendor_routing_executed=False,device_programmed=False,own_pnr_result=False)
    if result['status']=='captured':result['capture_sha256']=sha(out/'standalone_timing.json')
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()
