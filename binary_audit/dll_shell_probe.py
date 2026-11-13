import sys,os,ctypes
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BIN=Path('C:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin')
os.add_dll_directory(str(BIN));os.chdir(BIN)
tcl=ctypes.CDLL(str(BIN/'tcl86t.dll'))
tcl.Tcl_CreateInterp.restype=ctypes.c_void_p
tcl.Tcl_FindExecutable.argtypes=[ctypes.c_char_p];tcl.Tcl_FindExecutable(str(BIN/'gw_sh.exe').encode())
tcl.Tcl_Init.argtypes=[ctypes.c_void_p]
tcl.Tcl_Eval.argtypes=[ctypes.c_void_p,ctypes.c_char_p]
tcl.Tcl_GetStringResult.argtypes=[ctypes.c_void_p];tcl.Tcl_GetStringResult.restype=ctypes.c_char_p
interp=tcl.Tcl_CreateInterp();print('TCL_INIT',tcl.Tcl_Init(interp),flush=True)
gw=ctypes.CDLL(str(BIN/'gwsyn.dll'));init=getattr(gw,'?TCL_appInit@SYN@@YAHPEAUTcl_Interp@@@Z');init.argtypes=[ctypes.c_void_p];print('GW_INIT',init(interp),flush=True)
for cmd in ['extract -h','output -h','dev -h','cvt -h','infer -h','map -h','dio -h','ato -h']:
 print('COMMAND',cmd,flush=True);ret=tcl.Tcl_Eval(interp,cmd.encode());print('RESULT',ret,tcl.Tcl_GetStringResult(interp).decode(errors='replace'),flush=True)
