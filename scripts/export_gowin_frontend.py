"""Export the Gowin elaborated/GAO-merged design without running its mapper."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from gowin_probe import ROOT, BIN, PART, DEVICE

EXPECTED = '254eac1ed05c7d1168889473dcc85203032fa4cd641c83bd5bbe4ea61a55b6b0'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quote(path):
    value = Path(path).resolve().as_posix()
    if any(c in value for c in '{}\r\n'):
        raise ValueError('Unsupported Tcl path')
    return '{'+value+'}'


def project_inputs(project):
    tree = ET.parse(project)
    device = tree.find('.//Device')
    if device is None or device.attrib.get('partNumber') != PART:
        raise ValueError('This frontend flow is pinned to '+PART)
    options = {}
    for item in tree.findall('.//Option'):
        options.setdefault(item.attrib['type'], []).append(item.attrib['value'])
    sources, gao, vhdl = [], [], []
    for item in tree.findall('.//File'):
        path = Path(item.attrib['path'])
        if not path.is_absolute():
            path = project.parent/path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        kind = item.attrib['type']
        if kind == 'gao':
            gao.append(path)
        elif kind == 'verilog':
            sources.append(path)
        elif kind == 'vhdl':
            vhdl.append(path)
        else:
            raise ValueError('Unimplemented synthesis project file type: '+kind)
    if vhdl:
        raise ValueError('This project importer currently requires Verilog inputs')
    if not sources:
        raise ValueError('No Verilog sources')
    if gao and gao[-1].name != 'gw_gao_top.v':
        raise ValueError('Expected generated RTL_GAO wrapper as final GAO input')
    known = {'disable_insert_pad','dsp_style','global_freq','include_path','looplimit',
             'map_option','max_fanout','netlist_hierarchy','opt_goal','output_file',
             'print_all_synthesis_warning','ram_rw_check','ram_style','rom_style',
             'shift_reg','top_module','vcc','verilog_language','vhdl_language'}
    unknown = set(options)-known
    if unknown:
        raise ValueError('Review unhandled project options: '+str(sorted(unknown)))
    return sources, gao, options


def export(project, out, timeout=600):
    from flow_budget import clipped
    timeout=clipped(timeout)
    project = project.resolve()
    sources, gao, options = project_inputs(project)
    out.mkdir(parents=True, exist_ok=False)
    if sha(BIN/'gwsyn.dll') != EXPECTED:
        raise ValueError('Gowin synthesis DLL changed; revalidate frontend interface')
    top = options.get('top_module', ['top'])[-1]
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', top):
        raise ValueError('Unsupported top identifier')
    snapshots = {str(p): sha(p) for p in sources+gao+[project]}
    wrapper = out/'frontend_inputs.v'
    wrapper.write_text('\n'.join('`include "'+p.as_posix()+'"' for p in sources+gao[:-1])+'\n')
    # This build's extract interface does not reliably honor a list of -inc
    # values. Stage a checked union of the project's explicit include roots.
    roots = sorted(set(Path(p).resolve() for p in options.get('include_path', []))
                   | ({gao[-1].parent} if gao else set()))
    include = out/'includes'
    include.mkdir()
    staged = {}
    total = 0
    for directory in roots:
        if out.is_relative_to(directory):
            raise ValueError('Include root contains the output directory; choose a separate include root')
        for source in directory.rglob('*'):
            if not source.is_file():continue
            relative = source.relative_to(directory)
            key = relative.as_posix().casefold()
            data = source.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if key in staged and staged[key] != digest:
                raise ValueError('Conflicting include files: '+str(relative))
            total += len(data)
            if total > 256*1024*1024 or len(staged)>10000:
                raise ValueError('Include snapshot exceeds the bounded importer budget')
            target = include/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
            staged[key] = digest
            snapshots[str(source)] = digest
    destination = out/'elaborated.v'
    command = 'extract -i '+quote(wrapper)
    if gao:
        command += ' -gao '+quote(gao[-1])
    if roots:
        command += ' -inc '+quote(include)
    command += ' -verilog_language '+options.get('verilog_language', ['verilog-2001'])[-1]
    command += ' -e '+top+' -o '+quote(destination)
    commands = [f'dev -p {DEVICE} -pn {PART}', command]
    script = out/'frontend.tcl'
    script.write_text('\n'.join(commands)+'\n')
    result = dict(schema='gowin-frontend-export-v1', status='running', top=top,
        boundary='After elaboration and optional RTL GAO merge; before cvt/infer/map',
        synthesis_dll_sha256=EXPECTED, inputs=snapshots, commands=commands,
        staged_include_files=staged,
        gao_wrapper=str(gao[-1]) if gao else None,
        gao_generation='Consumes vendor-generated project inputs; does not reinterpret .rao',
        runner_sha256=sha(Path(__file__)))
    def save():
        (out/'manifest.json').write_text(json.dumps(result, indent=2)+'\n')
    save()
    python = ROOT/'binary_audit/flow/runtime/bin/python.exe'
    start = time.perf_counter()
    with (out/'frontend.log').open('w') as log:
        cp = subprocess.run([str(python), str(ROOT/'binary_audit/dll_steps.py'), str(script)],
            cwd=python.parent, env=dict(os.environ, PYTHONHOME=sys.base_prefix),
            stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    result.update(wall_seconds=time.perf_counter()-start, returncode=cp.returncode)
    log = (out/'frontend.log').read_text(errors='replace')
    result['errors'] = re.findall(r'^ERROR\s*\(.*$', log, re.M)
    result['inputs_unchanged'] = all(sha(Path(p)) == digest for p, digest in snapshots.items())
    ok = cp.returncode == 0 and not result['errors'] and destination.is_file() and result['inputs_unchanged']
    if ok:
        data = destination.read_text(encoding='utf-8')
        ok = bool(re.search(r'\bmodule\s+'+re.escape(top)+r'\s*\(', data))
        result['elaborated_sha256'] = sha(destination)
        result['bytes'] = destination.stat().st_size
    result['status'] = 'complete' if ok else 'failed'
    save()
    if not ok:
        raise RuntimeError('Frontend export failed: '+str(out/'frontend.log'))
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('project', type=Path, help='Generated Gowin synthesis .prj, not .gprj')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--timeout', type=int, default=600)
    args = ap.parse_args()
    print(json.dumps(export(args.project, args.out.resolve(), args.timeout), indent=2))


if __name__ == '__main__':
    main()
