"""Run controlled, out-of-process Gowin DLL probes without changing the install."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

# The isolated executable needs Conda's dependency directories before _ctypes.
_dll_handles = [os.add_dll_directory(str(p)) for p in
                (Path(sys.base_prefix)/'DLLs', Path(sys.base_prefix)/'Library/bin')
                if os.name == 'nt' and p.exists()]
import ctypes

ROOT = Path(__file__).resolve().parents[1]
from gowin_installation import SDK
BIN = SDK/'bin'
DEVICE = 'GW5AST-138C-FCPBGA676A-1'
PART = 'GW5AST-LV138FPG676AC1/I0'


def worker(mode, directory):
    handles = [os.add_dll_directory(str(p)) for p in
               (Path(sys.base_prefix)/'DLLs', Path(sys.base_prefix)/'Library/bin', BIN)]
    if mode == 'synth':
        tcl = ctypes.CDLL(str(BIN/'tcl86t.dll'))
        tcl.Tcl_CreateInterp.restype = ctypes.c_void_p
        tcl.Tcl_FindExecutable.argtypes = [ctypes.c_char_p]
        tcl.Tcl_FindExecutable(str(BIN/'gw_sh.exe').encode())
        tcl.Tcl_Init.argtypes = [ctypes.c_void_p]
        tcl.Tcl_Eval.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        tcl.Tcl_GetStringResult.argtypes = [ctypes.c_void_p]
        tcl.Tcl_GetStringResult.restype = ctypes.c_char_p
        interp = tcl.Tcl_CreateInterp()
        if tcl.Tcl_Init(interp):
            raise RuntimeError(tcl.Tcl_GetStringResult(interp))
        dll = ctypes.CDLL(str(BIN/'gwsyn.dll'))
        init = getattr(dll, '?TCL_appInit@SYN@@YAHPEAUTcl_Interp@@@Z')
        init.argtypes = [ctypes.c_void_p]
        if init(interp):
            raise RuntimeError('Gowin Tcl init failed')
        for command in (directory/'synth.tcl').read_text().splitlines():
            print('COMMAND', command, flush=True)
            result = tcl.Tcl_Eval(interp, command.encode())
            print('RESULT', result, tcl.Tcl_GetStringResult(interp).decode(errors='replace'), flush=True)
            if result:
                raise RuntimeError(command)
    else:
        os.chdir(directory)
        dll = ctypes.CDLL(str(BIN/'gowin.dll'))
        parse = getattr(dll, '?readInUserCommand@@YAHHPEAPEAD_N@Z')
        parse.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p), ctypes.c_bool]
        parse.restype = ctypes.c_int
        args = [str(BIN/'gowin.exe'), '-do', str(directory/'cmd.do')]
        argv = (ctypes.c_char_p*len(args))(*(a.encode() for a in args))
        if parse(len(args), argv, True):
            raise RuntimeError('P&R argument parsing failed')
        run = getattr(dll, '?executePnrTask@@YAHXZ')
        run.restype = ctypes.c_int
        rc = run()
        getattr(dll, '?closeLogFile@@YAXXZ')()
        if rc:
            raise RuntimeError(f'P&R returned {rc}')


def prepare(directory):
    directory.mkdir(parents=True, exist_ok=False)
    # Every LUT input gets an independent launch register. XOR uses all pins.
    lines = ['module top(clk, a, q);', 'input clk;', 'input [3:0] a;',
             'output [3:0] q;', 'wire ck, vcc, gnd;', 'wire [3:0] ai, r, f, z;',
             'IBUF clk_ibuf(.I(clk), .O(ck));', 'VCC vc(.V(vcc));',
             'GND gc(.G(gnd));', 'GSR gs(.GSRI(vcc));']
    for i in range(4):
        lines += [f'IBUF in{i}(.I(a[{i}]), .O(ai[{i}]));',
                  f'DFFRE launch{i}(.D(ai[{i}]), .CLK(ck), .CE(vcc), .RESET(gnd), .Q(r[{i}]));']
    for k in range(1, 5):
        init = sum((n.bit_count() % 2) << n for n in range(1 << k))
        ports = ', '.join(f'.I{i}(r[{i}])' for i in range(k))
        lines += [f'LUT{k} lut{k}({ports}, .F(f[{k-1}]));',
                  f"defparam lut{k}.INIT={1<<k}'h{init:X};",
                  f'DFFRE capture{k}(.D(f[{k-1}]), .CLK(ck), .CE(vcc), .RESET(gnd), .Q(z[{k-1}]));',
                  f'OBUF out{k}(.I(z[{k-1}]), .O(q[{k-1}]));']
    (directory/'probe.vg').write_text('\n'.join(lines+['endmodule'])+'\n')
    (directory/'probe.sdc').write_text('create_clock -name probe_clk -period 10 [get_ports {clk}]\n'
        'set_input_delay -max 1 -clock probe_clk [get_ports {a[*]}]\n'
        'set_input_delay -min 0 -clock probe_clk [get_ports {a[*]}]\n'
        'set_output_delay -max 1 -clock probe_clk [get_ports {q[*]}]\n'
        'set_output_delay -min 0 -clock probe_clk [get_ports {q[*]}]\n')
    args = [f'-d {directory.as_posix()}/probe.vg', f'-p {DEVICE}', f'-pn {PART}',
            '-tr', '-timing', '-place_option 3', '-route_option 2',
            '-ireg_not_in_iob', '-oreg_not_in_iob', '-ioreg_not_in_iob',
            f'-sdc {directory.as_posix()}/probe.sdc',
            f'-o {directory.as_posix()}/probe.vo']
    (directory/'cmd.do').write_text('\n'.join(args)+'\n')
    (directory/'rtl.v').write_text('module top(input clk, input [7:0] a, output reg q);\n'
        'always @(posedge clk) q <= ((a[0]&a[1])^(a[2]&a[3])) | ((a[4]&a[5])^(a[6]&a[7]));\nendmodule\n')
    commands = [f'dev -p {DEVICE} -pn {PART}',
        f'extract -i {{{directory.as_posix()}/rtl.v}} -e top -o {{{directory.as_posix()}/elaborated.v}}',
        'cvt', 'dio -novgopt', 'infer -ram -dsp', 'ato', 'dio', 'map -t',
        'dio -divfanout', f'output -v {{{directory.as_posix()}/mapped.vg}}']
    (directory/'synth.tcl').write_text('\n'.join(commands)+'\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--worker', choices=['synth', 'pnr'])
    args = ap.parse_args()
    directory = args.out.resolve()
    if args.worker:
        worker(args.worker, directory)
        return
    prepare(directory)
    python = ROOT/'binary_audit/flow/runtime/bin/python.exe'
    env = dict(os.environ, PYTHONHOME=sys.base_prefix)
    manifest = {'part': PART, 'device': DEVICE, 'runs': [], 'dll_sha256': {}}
    for name in ['gwsyn.dll', 'gowin.dll']:
        manifest['dll_sha256'][name] = hashlib.sha256((BIN/name).read_bytes()).hexdigest()
    for mode in ['synth', 'pnr']:
        started = time.time()
        net_hash = hashlib.sha256((directory/'probe.vg').read_bytes()).hexdigest()
        with (directory/f'{mode}.log').open('w') as log:
            cp = subprocess.run([str(python), str(Path(__file__).resolve()), '--worker', mode,
                '--out', str(directory)], cwd=python.parent, env=env, stdout=log,
                stderr=subprocess.STDOUT, timeout=300)
        logtext = (directory/f'{mode}.log').read_text(errors='replace')
        expected = ['mapped.vg', 'elaborated.v'] if mode == 'synth' else ['probe.tr', 'probe.vo']
        unchanged = hashlib.sha256((directory/'probe.vg').read_bytes()).hexdigest() == net_hash
        ok = (cp.returncode == 0 and 'ERROR (' not in logtext and unchanged
              and all((directory/name).exists() and (directory/name).stat().st_size > 0 for name in expected))
        manifest['runs'].append({'mode': mode, 'exit_code': cp.returncode,
            'ok': ok, 'input_unchanged': unchanged, 'elapsed_seconds': time.time()-started})
        (directory/'manifest.json').write_text(json.dumps(manifest, indent=2))
        if not ok:
            raise RuntimeError(f'{mode} failed; see {directory}/{mode}.log')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
