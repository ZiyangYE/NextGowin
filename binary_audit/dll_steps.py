import sys,os; os.add_dll_directory("D:/Conda3/DLLs"); os.add_dll_directory("D:/Conda3/Library/bin"); import ctypes
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BIN=Path('C:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin')
script_path=Path(sys.argv[1]).resolve();os.add_dll_directory(str(BIN));os.chdir(BIN)
tcl=ctypes.CDLL(str(BIN/'tcl86t.dll'))
tcl.Tcl_CreateInterp.restype=ctypes.c_void_p
tcl.Tcl_FindExecutable.argtypes=[ctypes.c_char_p];tcl.Tcl_FindExecutable(str(BIN/'gw_sh.exe').encode())
tcl.Tcl_Init.argtypes=[ctypes.c_void_p]
tcl.Tcl_Eval.argtypes=[ctypes.c_void_p,ctypes.c_char_p]
tcl.Tcl_GetStringResult.argtypes=[ctypes.c_void_p];tcl.Tcl_GetStringResult.restype=ctypes.c_char_p
interp=tcl.Tcl_CreateInterp();print('TCL_INIT',tcl.Tcl_Init(interp),flush=True)
gw=ctypes.CDLL(str(BIN/'gwsyn.dll'));init=getattr(gw,'?TCL_appInit@SYN@@YAHPEAUTcl_Interp@@@Z');init.argtypes=[ctypes.c_void_p];print('GW_INIT',init(interp),flush=True)
commands=script_path.read_text(encoding="utf-8-sig").splitlines()
for cmd in commands:
 if not cmd.strip() or cmd.lstrip().startswith("#"):continue
 print("COMMAND",cmd,flush=True)
 ret=tcl.Tcl_Eval(interp,cmd.encode())
 print("RESULT",ret,tcl.Tcl_GetStringResult(interp).decode(errors="replace"),flush=True)
 if ret: raise SystemExit(ret)
