"""Delay-reconstructed, box-aware ABC9 mapping with native interface guards.

This is an optional mapping method, not a claim of superiority. It consumes our
resource-inferred graph and has no dependency on a vendor-synthesized netlist.
"""
import copy
import json
from pathlib import Path
import re
import time

from gowin_cones import masked
from gowin_netlist_format import LIBRARY, EXPECTED
from gw5a_aig_rewrite import rewrite
from gw5a_abc9_models import generate
from map_resource_json import FUNCTIONS
from lower_carry_only import lower
from native_boundary_cec import prove
from run_abc_reproduction import ROOT, sha
from verific_bridge import units
from yosys_runtime import run, quote


def native_interfaces(path, kinds):
    """Use the pinned SDK ABI, including parameters and bidirectional ports."""
    if sha(LIBRARY) != EXPECTED:
        raise ValueError('Native functional library changed')
    text = LIBRARY.read_text(encoding='utf-8')
    rows = []
    found = set()
    for name, (a, b) in units(text).items():
        if name not in kinds:
            continue
        body = masked(text[a:b])
        declarations = re.findall(r'^\s*(?:parameter|localparam|input|output|inout)\b[^;]*;', body, re.M)
        rows.append('(* blackbox=1 *) '+body[:body.index(';')+1]+'\n'+'\n'.join(declarations)+'\nendmodule\n')
        found.add(name)
    path.write_text('\n'.join(rows))
    return found


def scoped_candidate(before, mapped, active):
    """Restore all modules outside the explicitly mapped user scope exactly."""
    if set(before['modules']) - set(mapped['modules']):
        raise ValueError('ABC9 removed input module definitions')
    reference = copy.deepcopy(before)
    result = copy.deepcopy(before)
    zeros = [0, 0]
    for name in active:
        result['modules'][name] = copy.deepcopy(mapped['modules'][name])
        result['modules'][name]['attributes'] = copy.deepcopy(before['modules'][name].get('attributes', {}))
        # Match the emitter's established zero-fill policy only in modules
        # participating in the proof. Opaque IP remains byte-for-byte exact.
        for index, design in enumerate((reference, result)):
            for cell in design['modules'][name].get('cells', {}).values():
                for pin, bits in cell['connections'].items():
                    if cell['port_directions'].get(pin) not in ('input', 'inout'):
                        continue
                    for i, bit in enumerate(bits):
                        if bit in ('x', 'z'):
                            bits[i] = '0'
                            zeros[index] += 1
    return reference, result, zeros


def map_design(source, out, vendor_library, dsp_library=None, period_ps=None):
    start = time.perf_counter()
    source, out = Path(source), Path(out)
    out.mkdir(parents=True, exist_ok=False)
    original = json.loads(source.read_text())
    lowered, lowering = lower(original)
    active = [name for name, module in lowered['modules'].items()
              if not int(module.get('attributes', {}).get('blackbox', '0'), 2)
              and any(c['type'] in FUNCTIONS for c in module.get('cells', {}).values())]
    record = dict(schema='gw5a-box-mapping-v1', status='running', strategy='abc9_box',
                  source_sha256=sha(source), vendor_synthesis_seed=False,
                  model_sha256=sha(ROOT/'results/timing/gowin138_model.json'),
                  period_ps=period_ps, all_local_budgets_satisfied=None,
                  comparison_lowering=lowering,
                  timing_scope='Cell/pin/FF-box delay-driven ABC9. Common boundary target only; original SDC and hold are checked by the native physical backend.',
                  mapped_user_modules=active, modules=[],
                  implementation_sha256={p.name: sha(p) for p in [Path(__file__).resolve(), ROOT/'scripts/gw5a_aig_rewrite.py', ROOT/'scripts/gw5a_abc9_models.py', ROOT/'scripts/native_boundary_cec.py', ROOT/'scripts/lower_carry_only.py']})
    def save():
        (out/'manifest.json').write_text(json.dumps(record, indent=2)+'\n')
    save()
    try:
        timing_library = generate(out/'timing_models')
        record['timing_models'] = json.loads((out/'timing_models/manifest.json').read_text())
        if active:
            rebuilt, rewriting = rewrite(lowered, out/'rewrite')
            record['rewrite_proofs'] = rewriting
            (out/'rewritten.json').write_text(json.dumps(rebuilt)+'\n')
            vendor_library = Path(vendor_library)
            supplied = set(re.findall(r'\bmodule\s+(\\\S+|[A-Za-z_$][\w$]*)', masked(vendor_library.read_text(encoding='utf-8'))))
            used = {c['type'] for m in rebuilt['modules'].values()
                    if not int(m.get('attributes', {}).get('blackbox', '0'), 2)
                    for c in m.get('cells', {}).values()}
            native_library = out/'native_interfaces.v'
            record['sdk_interfaces'] = sorted(native_interfaces(native_library, used-supplied))
            libraries = ['read_verilog -overwrite -lib '+quote(native_library),
                         'read_verilog -overwrite -lib '+quote(vendor_library)]
            if dsp_library and Path(dsp_library).exists():
                libraries.append('read_verilog -overwrite -lib '+quote(dsp_library))
            delay = '' if period_ps is None else ' -D '+str(int(period_ps))
            run(['read_json '+quote(out/'rewritten.json'),
                 *['setattr -mod -unset '+attr+' =*' for attr in ('abc9_box', 'abc9_box_id', 'abc9_lut', 'abc9_flop')],
                 *libraries, 'read_verilog -overwrite -specify -lib '+quote(timing_library),
                 'sort', 'read_verilog -icells -lib -specify +/abc9_model.v',
                 'select '+' '.join(active)+' A:abc9_lut A:abc9_box A:abc9_box_id A:abc9_flop',
                 'abc9 -maxlut 4 -W 500'+delay,
                 'techmap -map +/gowin/cells_map.v', 'opt_lut_ins -tech gowin', 'clean',
                 'select *', 'bufnorm -conn', 'blackbox =A:whitebox',
                 'write_json '+quote(out/'global_mapped.json')], out, 'abc9', 900)
            mapped = json.loads((out/'global_mapped.json').read_text())
            reference, candidate, zeros = scoped_candidate(original, mapped, active)
            record['undefined_input_zero_fill'] = zeros
            (out/'proof_reference.json').write_text(json.dumps(reference)+'\n')
            record['proof_reference_sha256'] = sha(out/'proof_reference.json')
            proofs = prove(reference, candidate, out/'native_proof', 300)
            record['native_proofs'] = proofs
            record['modules'] = [dict(name=name) for name in active]
            for name in active:
                if any(c['type'] in FUNCTIONS for c in candidate['modules'][name]['cells'].values()):
                    raise ValueError('Unmapped Boolean cells remain: '+name)
        else:
            candidate = original
            (out/'proof_reference.json').write_text(json.dumps(original)+'\n')
            record['proof_reference_sha256'] = sha(out/'proof_reference.json')
            record['native_proofs'] = []
        target = out/'mapped.json'
        target.write_text(json.dumps(candidate)+'\n')
        record.update(status='complete', output_sha256=sha(target), wall_seconds=time.perf_counter()-start)
        save()
        return record
    except Exception as error:
        record.update(status='failed', error=repr(error), wall_seconds=time.perf_counter()-start)
        save()
        raise
