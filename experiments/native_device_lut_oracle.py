"""Calibrate native LUT encodings without calling a placer or router.

Uses isolated initialized physical resources and the native encoder. Resulting
images are calibration artifacts, not routed designs and never programmed.
"""
import argparse,gzip,json,os,random,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import BIN,prepare
from capture_runtime_tables import EXPECTED
from run_abc_reproduction import sha


def worker(out,mode_sweep=False,debug_normalization=False):
    handles=[os.add_dll_directory(str(p)) for p in (Path(sys.base_prefix)/'DLLs',Path(sys.base_prefix)/'Library/bin',BIN)]
    import ctypes as C
    C.windll.kernel32.SetErrorMode(0x8003)
    if sha(BIN/'gowin.dll')!=EXPECTED:raise ValueError('Unreviewed native ABI')
    os.chdir(out);dll=C.CDLL(str(BIN/'gowin.dll'));vp=C.c_void_p
    def fn(name,result,args):
        f=getattr(dll,name);f.restype=result;f.argtypes=args;return f
    def ptr(address):return C.c_uint64.from_address(address).value
    def typ(obj):
        vt=ptr(obj);loc=ptr(vt-8);td=C.c_uint32.from_address(loc+12).value
        if not dll._handle<=vt<dll._handle+0x2000000 or td>=0x2000000:raise ValueError('Native type outside image')
        return C.string_at(dll._handle+td+16).decode('ascii')
    args=[str(BIN/'gowin.exe'),'-do',str(out/'cmd.do')];av=(C.c_char_p*len(args))(*(s.encode() for s in args))
    if fn('?readInUserCommand@@YAHHPEAPEAD_N@Z',C.c_int,[C.c_int,C.POINTER(C.c_char_p),C.c_bool])(len(args),av,True):raise ValueError('Parser failed')
    mem=fn('?getGlobalMemPtr@@YAPEAVglobalMem@@XZ',vp,[])()
    if fn('?readDevice@NETLIST@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])(mem):raise ValueError('Device loading failed')
    # Native serializer's fifth argument is globalMem+0x80 (configuration
    # attributes). Its constructor is this normal initialization stage.
    if fn('?readConfigAttFile@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])(mem):raise ValueError('Configuration attributes failed')
    # Encoder construction also dereferences an initialized Design object.
    # Parse a netlist to construct that container; do not place or route it.
    if fn('?readInput@VLGPARSE@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])(mem):raise ValueError('Design initialization failed')
    chip=fn('?getDevChip@globalMem@@QEAAPEAVchip@NETLIST@@XZ',vp,[vp])(mem)
    getgrid=fn('?getGridByRowCol@chip@NETLIST@@QEAAPEAVgrid@2@FF@Z',vp,[vp,C.c_short,C.c_short])
    from extract_native_fse import Fse
    fse=Fse();sites={}
    for r,row in enumerate(fse.grid):
        for c,tile in enumerate(row):
            if tile in sites:continue
            grid=getgrid(chip,r+1,c+1)
            if grid and C.c_int.from_address(grid+0x18).value==5:sites[tile]=(r+1,c+1,grid)
    targets=[];objects=[]
    used=C.CFUNCTYPE(C.c_bool,vp,C.c_ubyte)(dll._handle+0x221fd0)
    for tile,(r,c,grid) in sorted(sites.items()):
        aggregate=ptr(grid+0x70)
        for slot in range(8):
            lut=ptr(aggregate+8*slot)
            if typ(lut)!='.?AVlut4@NETLIST@@' or struct.unpack('<4H',C.string_at(lut+0x10,8))!=(24,r,c,slot):raise ValueError('LUT type/position mismatch')
            # In this guarded build, +0x22 enables a physical LUT mode without
            # a logical-instance pointer; native table-5 mode 10 reads +0x20.
            C.c_ubyte.from_address(lut+0x22).value=1
            C.c_ubyte.from_address(aggregate+0x326+slot).value=1
            if not used(aggregate,slot):raise ValueError('Native LUT used flag not recognized')
            init=0x1357^(slot*0x1111);C.c_ushort.from_address(lut+0x20).value=init
            objects.append((lut,init));targets.append(dict(name=f'calibration_{tile}_{slot}',physical_header=[24,r,c,slot],original_init=init,tile_type=tile))
    print('DEVICE_ONLY_LUTS',len(targets),'TILE_TYPES',sorted(sites),flush=True)
    (out/'targets.json').write_text(json.dumps(targets,indent=2))
    encode=fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[vp]);fs=out/'probe.fs'
    if encode(mem):raise ValueError('Device-only baseline encoding failed')
    if not fs.exists():raise ValueError('No native image')
    # First encoding changes native state even without a P&R task. Preserve it
    # and use a separately regenerated baseline for reversible differential
    # inference; do not treat that baseline as a complete design image.
    with fs.open('rb') as src,gzip.open(out/'encoding_first.fs.gz','wb',compresslevel=1) as dst:shutil.copyfileobj(src,dst)
    if encode(mem):raise ValueError('Device-only baseline stabilization failed')
    shutil.copyfile(fs,out/'encoding_baseline.fs');captures=[]
    if debug_normalization:
        aggregate=ptr(getgrid(chip,2,2)+0x70);group=ptr(aggregate+0x360)
        C.c_short.from_address(group+6).value=11;C.c_short.from_address(group+8).value=12
        C.c_short.from_address(group+0x24).value=18
        print('NORMALIZATION_WATCH',hex(group+0x24),flush=True)
        C.windll.kernel32.DebugBreak()
        encode(mem)
        raise RuntimeError('Debugger did not stop at the normalization write')
    if mode_sweep:
        groups=[];mode_objects=[]
        for tile,(r,c,grid) in sorted(sites.items()):
            aggregate=ptr(grid+0x70)
            for group in range(4):
                address=ptr(aggregate+0x360+8*group)
                if not address:raise ValueError('Native CLS configuration absent')
                attributes=list(struct.unpack('<19h',C.string_at(address+6,38)))
                mode_objects.append(address);groups.append(dict(tile_type=tile,native_site=[r,c],group=group,attributes=attributes))
        domains={i:{0} for i in range(19)}
        for attr,value,_ in list(fse.read_rows(fse.get_table('header',12)))[1:]:domains[attr].add(value)
        domains={i:sorted(v) for i,v in domains.items()};rng=random.Random(138004)
        for trial in range(8):
            vectors=[]
            for address in mode_objects:
                values=[rng.choice(domains[i]) for i in range(19)];vectors.append(values)
                C.memmove(address+6,struct.pack('<19h',*values),38)
            if encode(mem):raise ValueError('CLS calibration encoding failed')
            encoded_vectors=[list(struct.unpack('<19h',C.string_at(address+6,38))) for address in mode_objects]
            path=out/f'encoding_cls_{trial:02}.fs.gz'
            with fs.open('rb') as src,gzip.open(path,'wb',compresslevel=1) as dst:shutil.copyfileobj(src,dst)
            captures.append(dict(file=path.name,sha256=sha(path),attributes=vectors,encoded_attributes=encoded_vectors,
                rewritten_attributes=sum(a!=b for before,after in zip(vectors,encoded_vectors) for a,b in zip(before,after))))
            print('CLS_CAPTURED',trial,'REWRITES',captures[-1]['rewritten_attributes'],flush=True)
        for address,group in zip(mode_objects,groups):C.memmove(address+6,struct.pack('<19h',*group['attributes']),38)
        if encode(mem):raise ValueError('CLS restoration failed')
        shutil.copyfile(fs,out/'encoding_restored.fs')
        report=dict(groups=groups,captures=captures,dll_sha256=EXPECTED,source_fse_sha256=fse.sha256,
            baseline_sha256=sha(out/'encoding_baseline.fs'),restored_sha256=sha(out/'encoding_restored.fs'),open_chip_database_used=False,
            vendor_placement_executed=False,vendor_routing_executed=False,scope='Raw CLS feature-vector differential calibration. Primitive semantics and legality are not inferred from arbitrary feature vectors.')
        (out/'cls_mode_sweep.json').write_text(json.dumps(report,indent=2));fn('?closeLogFile@@YAXXZ',None,[])();return
    for mask in (0xffff,0xaaaa,0xcccc,0xf0f0,0xff00):
        for lut,init in objects:C.c_ushort.from_address(lut+0x20).value=init^mask
        if encode(mem):raise ValueError('Device-only variant encoding failed')
        path=out/f'encoding_mask_{mask:04x}.fs.gz'
        with fs.open('rb') as src,gzip.open(path,'wb',compresslevel=1) as dst:shutil.copyfileobj(src,dst)
        captures.append(dict(mask=mask,file=path.name,sha256=sha(path)));print('CAPTURED',hex(mask),flush=True)
    for lut,init in objects:C.c_ushort.from_address(lut+0x20).value=init
    if encode(mem):raise ValueError('Device-only restore encoding failed')
    shutil.copyfile(fs,out/'encoding_restored.fs')
    report=dict(targets=targets,captures=captures,dll_sha256=EXPECTED,source_fse_sha256=fse.sha256,
        baseline_sha256=sha(out/'encoding_baseline.fs'),restored_sha256=sha(out/'encoding_restored.fs'),
        open_chip_database_used=False,vendor_placement_executed=False,vendor_routing_executed=False,
        placement_routing_rerun_during_mutation=False,device_programmed=False,
        warmup_encodes=1,scope='Device-only physical LUT truth-bit calibration after one warmup encoding. No design, routing, IO, timing or hardware signoff.')
    (out/'lut_init_sweep.json').write_text(json.dumps(report,indent=2))
    fn('?closeLogFile@@YAXXZ',None,[])()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--worker',action='store_true');ap.add_argument('--mode-sweep',action='store_true');ap.add_argument('--debug-normalization',action='store_true');a=ap.parse_args();out=a.out.resolve()
    if a.debug_normalization and not a.worker:ap.error('Debug probe requires an attached worker debugger')
    if a.worker:return worker(out,a.mode_sweep,a.debug_normalization)
    prepare(out);cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+'-bit\n')
    shutil.copyfile(__file__,out/'extractor_source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;timeout=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)]+(['--mode-sweep'] if a.mode_sweep else []),cwd=python.parent,
            env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=180).returncode
        except subprocess.TimeoutExpired:timeout=True
    result=dict(status='captured' if rc==0 and (out/('cls_mode_sweep.json' if a.mode_sweep else 'lut_init_sweep.json')).exists() else 'failed',returncode=rc,timeout=timeout,
        wall_seconds=time.monotonic()-start,dll_sha256=EXPECTED,open_chip_database_used=False,vendor_placement_executed=False,vendor_routing_executed=False,device_programmed=False)
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()
