import os,sys,hashlib,json,time
from pathlib import Path
os.add_dll_directory('D:/Conda3/DLLs');os.add_dll_directory('D:/Conda3/Library/bin')
import ctypes
ROOT=Path(__file__).resolve().parent
BIN=Path('C:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin');os.add_dll_directory(str(BIN))
out=ROOT/'direct_pnr';out.mkdir(exist_ok=True);os.chdir(out)
gw=ctypes.CDLL(str(BIN/'gowin.dll'))
parse=getattr(gw,'?readInUserCommand@@YAHHPEAPEAD_N@Z');parse.argtypes=[ctypes.c_int,ctypes.POINTER(ctypes.c_char_p),ctypes.c_bool];parse.restype=ctypes.c_int
run=getattr(gw,'?executePnrTask@@YAHXZ');run.restype=ctypes.c_int
args=['-d',str(ROOT/'custom_mapped.vg'),'-p','GW5AST-138C-FCPBGA676A-1','-pn','GW5AST-LV138FPG676AC1/I0','-cfg',str(ROOT/'device.cfg'),'-gao_crc','1110111101100001','-rtl_gao','-bit','-tr','-ph','-timing','-ireg_not_in_iob','-oreg_not_in_iob','-ioreg_not_in_iob','-place_option','3','-route_option','2','-correct_hold','1','-route_maxfan','23','-global_freq','100.000','-sdc',str(ROOT/'probe.sdc'),'-o',str(out/'custom_mapped.vo')]
# Match the vendor proxy: executable name, -do, command file.
cmdfile=out/'cmd.do'
lines=[]
i=0
while i<len(args):
 line=args[i];i+=1
 if i<len(args) and not args[i].startswith('-'):line+=' '+args[i];i+=1
 lines.append(line)
cmdfile.write_text('\n'.join(lines)+'\n')
args=[str(BIN/'gowin.exe'),'-do',str(cmdfile)]
arr=(ctypes.c_char_p*len(args))(*[s.encode() for s in args]);print('ARGS',args,flush=True)
rc=parse(len(args),arr,True);print('PARSE_RESULT',rc,flush=True)
if rc:raise SystemExit(rc)
before=hashlib.sha256((ROOT/'custom_mapped.vg').read_bytes()).hexdigest(); started=time.time()
rc=run();print('PNR_RESULT',rc,flush=True)
after=hashlib.sha256((ROOT/'custom_mapped.vg').read_bytes()).hexdigest()
assert before==after, 'P&R modified input netlist'
(out/'invocation.json').write_text(json.dumps({'parse_result':0,'pnr_result':rc,'input_sha256_before':before,'input_sha256_after':after,'started_epoch':started,'finished_epoch':time.time(),'entry':'gowin.dll readInUserCommand + executePnrTask','synthesis_invoked':False},indent=2))
getattr(gw,'?closeLogFile@@YAXXZ')()
raise SystemExit(rc)
