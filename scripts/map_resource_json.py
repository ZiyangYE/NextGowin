"""Single-threaded, CEC-checked ACD mapping of Yosys resource-inferred graphs.

Registers, carry cells, memories, IP, and hierarchy are cut boundaries. This
adapter maps Boolean gates, rather than remapping a vendor-mapped LUT design.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
import math
from pathlib import Path
from constrained_mapping import run
from gowin_blif import read_blif
from run_abc_reproduction import ROOT, cec, sha


FUNCTIONS = {
    '$_NOT_': ('A', lambda a: not a),
    '$_BUF_': ('A', lambda a: a),
    '$_AND_': ('AB', lambda a,b: a and b),
    '$_NAND_': ('AB', lambda a,b: not (a and b)),
    '$_OR_': ('AB', lambda a,b: a or b),
    '$_NOR_': ('AB', lambda a,b: not (a or b)),
    '$_XOR_': ('AB', lambda a,b: a != b),
    '$_XNOR_': ('AB', lambda a,b: a == b),
    '$_ANDNOT_': ('AB', lambda a,b: a and not b),
    '$_ORNOT_': ('AB', lambda a,b: a or not b),
    '$_MUX_': ('ABS', lambda a,b,s: b if s else a),
    '$_NMUX_': ('ABS', lambda a,b,s: not (b if s else a)),
}


def net(bit):
    if isinstance(bit, int):
        return 'b'+str(bit)
    if bit in ('0', '1'):
        return 'c'+bit
    raise ValueError('Undefined Boolean input requires an explicit semantic policy: '+repr(bit))


def extract(module):
    gates = {n:c for n,c in module.get('cells', {}).items() if c['type'] in FUNCTIONS}
    if not gates:
        return None
    outside = {b for p in module['ports'].values() for b in p['bits']}
    for name, cell in module['cells'].items():
        if name not in gates:
            outside.update(b for bits in cell['connections'].values() for b in bits)
    for row in module.get('netnames', {}).values():
        if 'init' in row.get('attributes', {}) or int(row.get('attributes', {}).get('keep', '0'), 2):
            outside.update(row['bits'])
    nodes = []
    driven = set()
    used = set()
    for name, cell in gates.items():
        pins, fun = FUNCTIONS[cell['type']]
        if set(cell['connections']) != set(pins)|{'Y'}:
            raise ValueError('Unexpected Boolean cell ports: '+name)
        if any(len(v) != 1 for v in cell['connections'].values()):
            raise ValueError('Expected bit-level Boolean cells')
        inputs = [net(cell['connections'][p][0]) for p in pins]
        output = net(cell['connections']['Y'][0])
        if output in driven or output in ('c0', 'c1'):
            raise ValueError('Invalid/multiply-driven Boolean gate output')
        truth = sum(int(fun(*[(a >> i)&1 for i in range(len(pins))])) << a
                    for a in range(1 << len(pins)))
        driven.add(output)
        used.update(inputs)
        nodes.append(dict(inputs=inputs, output=output, truth=truth))
    inputs = sorted(used-driven-{'c0','c1'})
    outputs = sorted(driven & {net(b) for b in outside if b not in ('x','z')})
    if not outputs:
        raise ValueError('Nonempty Boolean module without observable boundaries')
    lines = ['.model logic', '.inputs '+' '.join(inputs), '.outputs '+' '.join(outputs), '.names c0', '.names c1', '1']
    for node in nodes:
        lines.append('.names '+' '.join(node['inputs']+[node['output']]))
        for assignment in range(1 << len(node['inputs'])):
            if node['truth'] & (1 << assignment):
                lines.append(''.join(str((assignment >> i)&1) for i in range(len(node['inputs'])))+' 1')
    lines.append('.end')
    return dict(gates=gates, inputs=inputs, outputs=outputs, blif='\n'.join(lines)+'\n')


def replace(module, cut, network):
    result = copy.deepcopy(module)
    result['cells'] = {n:c for n,c in result['cells'].items() if n not in cut['gates']}
    maximum = max((b for row in module.get('netnames', {}).values() for b in row['bits'] if isinstance(b,int)), default=1)
    maximum = max(maximum, max((b for c in module['cells'].values() for bits in c['connections'].values() for b in bits if isinstance(b,int)), default=1))
    mapping = {'c0':'0','c1':'1'}
    mapping.update({name:int(name[1:]) for name in cut['inputs']+cut['outputs']})
    def bit(name):
        nonlocal maximum
        if name not in mapping:
            maximum += 1
            mapping[name] = maximum
        return mapping[name]
    for index, node in enumerate(network['nodes']):
        if node['output'] in ('c0', 'c1'):
            if node['inputs'] or node['truth'] != int(node['output'][-1]):
                raise ValueError('Literal constant net was changed by mapping')
            continue
        name = 'ng_acd_'+str(index)
        if name in result['cells']:
            raise ValueError('Generated cell name collision')
        k = len(node['inputs'])
        if k > 4:
            raise ValueError('Mapped LUT exceeds the native LUT4 interface')
        if node.get('native_mux') is not None:
            if k!=3 or node['truth']!=0xCA or node['native_mux'] not in (5,6):raise ValueError('Invalid architecture mux')
            cell=dict(type='MUX2_LUT'+str(node['native_mux']),hide_name=0,attributes={**({'keep':'1'} if node.get('keep') else {}),'ng_generated':'1'},parameters={},
                      port_directions=dict(I0='input',I1='input',S0='input',O='output'),
                      connections={p:[bit(v)] for p,v in zip(('I0','I1','S0','O'),node['inputs']+[node['output']])})
        elif k:
            cell = dict(type='LUT'+str(k), hide_name=0, attributes={**({'keep':'1'} if node.get('keep') else {}),'ng_generated':'1'},
                parameters=dict(INIT=format(node['truth'], '0'+str(1<<k)+'b')),
                port_directions={**{f'I{i}':'input' for i in range(k)}, 'F':'output'},
                connections={**{f'I{i}':[bit(n)] for i,n in enumerate(node['inputs'])}, 'F':[bit(node['output'])]})
        else:
            value = node['truth'] & 1
            cell = dict(type='VCC' if value else 'GND', hide_name=0, attributes={}, parameters={},
                port_directions={'V' if value else 'G':'output'}, connections={'V' if value else 'G':[bit(node['output'])]})
        result['cells'][name] = cell
    if any(result['cells'][n] != c for n,c in module['cells'].items() if n not in cut['gates']):
        raise ValueError('Non-Boolean resource changed')
    return result


def readback(module, cut):
    lines = ['.model readback', '.inputs '+' '.join(cut['inputs']), '.outputs '+' '.join(cut['outputs'])]
    reverse = {'0':'c0', '1':'c1'}
    def signal(bit):
        return reverse.get(bit, 'b'+str(bit))
    lines += ['.names c0', '.names c1', '1']
    for name, cell in module['cells'].items():
        if not name.startswith('ng_acd_'):
            continue
        if cell['type'] in ('VCC','GND'):
            pin = 'V' if cell['type']=='VCC' else 'G'
            output = signal(cell['connections'][pin][0])
            # Local c0/c1 declarations already represent literal constants.
            if output in ('c0','c1'):
                continue
            lines.append('.names '+output)
            if cell['type']=='VCC':
                lines.append('1')
            continue
        if cell['type'] in ('MUX2_LUT5','MUX2_LUT6'):
            if cell.get('parameters') or set(cell['connections'])!={'I0','I1','S0','O'}:raise ValueError('Malformed architecture mux')
            k=3;truth=0xCA;inputs=[signal(cell['connections'][p][0]) for p in ('I0','I1','S0')]
            output=signal(cell['connections']['O'][0])
        else:
            if cell['type'] not in ('LUT1','LUT2','LUT3','LUT4'):raise ValueError('Unknown generated primitive')
            k = int(cell['type'][3:])
            truth = int(cell['parameters']['INIT'],2)
            inputs = [signal(cell['connections'][f'I{i}'][0]) for i in range(k)]
            output = signal(cell['connections']['F'][0])
        lines.append('.names '+' '.join(inputs+[output]))
        if truth==0:
            lines.append('-'*k+' 0');continue
        for a in range(1<<k):
            if truth & (1<<a):
                lines.append(''.join(str((a>>i)&1) for i in range(k))+' 1')
    lines.append('.end')
    return '\n'.join(lines)+'\n'


def map_design(source, out, period_ps, strategy='acd_remap',architecture_recipe='wide'):
    if architecture_recipe not in ('wide','lut4','wide_no_recovery','lut4_local_wide','lut4_pins','wide_pins','local_wide_pins'):raise ValueError('Unknown architecture recipe')
    if strategy!='arch_delay' and architecture_recipe!='wide':raise ValueError('Architecture recipe requires arch_delay mapping')
    if not math.isfinite(period_ps) or period_ps <= 0:
        raise ValueError('Positive finite synthesis boundary budget required')
    if strategy=='abc9_box':
        if (source.parent/'register_choices.json').exists():
            raise ValueError('abc9_box currently requires register_alternatives=false: numeric choice bindings do not survive Yosys wire renumbering')
        from gw5a_box_mapper import map_design as box_map
        return box_map(source,out,source.parent.parent/'02_bridge/vendor_cells.v',
                       source.parent/'dsp_cells.v',period_ps)
    out.mkdir(parents=True, exist_ok=False)
    design = json.loads(source.read_text())
    result = dict(schema='resource-acd-mapping-v1', status='running', source_sha256=sha(source),
        execution='Single-threaded CPU; modules processed serially',
        timing_scope='Explicit common combinational boundary budget, not a general SDC interpreter. Full SDC is consumed separately by the native backend.',
        period_ps=period_ps, strategy=strategy, architecture_recipe=architecture_recipe,modules=[],
        model_sha256=sha(ROOT/'results/timing/gowin138_model.json'))
    def save():
        (out/'manifest.json').write_text(json.dumps(result, indent=2)+'\n')
    save()
    for name, module in design['modules'].items():
        if int(module.get('attributes',{}).get('blackbox','0'),2):
            continue
        unknown={c['type'] for c in module.get('cells',{}).values()
                 if c['type'].startswith('$') and c['type'] not in FUNCTIONS and c['type']!='$scopeinfo'}
        if unknown:
            raise ValueError('Unlowered cells remain after resource inference: '+str(sorted(unknown)))
        cut = extract(module)
        if cut is None:
            continue
        folder = out/('module_'+str(len(result['modules'])))
        folder.mkdir()
        original = folder/'source.blif'
        original.write_text(cut['blif'])
        timing = folder/'constraints.json'
        timing.write_text(json.dumps(dict(units='ps',default_input_arrival_ps=0,
                                         default_output_required_ps=period_ps)))
        if strategy=='arch_delay':
            from gw5a_arch_mapper import run as architecture_map
            mapped,network=architecture_map(original,timing,ROOT/'results/timing/gowin138_model.json',folder/'mapper',architecture_recipe,defer_cec=True)
        else:
            mapped = run(original, timing, ROOT/'results/timing/gowin138_model.json', folder/'mapper', strategy)
            network=read_blif(folder/'mapper/mapped_logic.blif')
        candidate = replace(module, cut, network)
        actual = folder/'readback.blif'
        actual.write_text(readback(candidate, cut))
        proof = cec(ROOT/'build/abc/_TEST/abc.exe', original, actual, folder/'readback_cec.log', 300)
        if not proof['equivalent']:
            raise RuntimeError('Actual JSON LUT INITs did not pass boundary CEC: '+name)
        design['modules'][name] = candidate
        result['modules'].append(dict(name=name, gate_count=len(cut['gates']),
            inputs=len(cut['inputs']), outputs=len(cut['outputs']), readback_cec=proof,
            non_boolean_cells_preserved=True, timing=mapped['independent_timing'],
            output_cell_counts=dict(Counter(c['type'] for c in candidate['cells'].values()))))
        save()
        print(name, len(cut['gates']), 'gates mapped; readback CEC passed', flush=True)
    target = out/'mapped.json'
    target.write_text(json.dumps(design)+'\n')
    result.update(status='complete', output_sha256=sha(target),
        all_local_budgets_satisfied=all(row['timing']['meets_required'] for row in result['modules']))
    save()
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--period-ps', type=float, required=True)
    ap.add_argument('--strategy', choices=['baseline','acd','acd_remap','arch_delay','abc9_box'], default='acd_remap')
    ap.add_argument('--architecture-recipe',choices=['wide','lut4','wide_no_recovery','lut4_local_wide','lut4_pins','wide_pins','local_wide_pins'],default='wide')
    args = ap.parse_args()
    result = map_design(args.input.resolve(), args.out.resolve(), args.period_ps, args.strategy,args.architecture_recipe)
    print('Mapped modules:', len(result['modules']))


if __name__ == '__main__':
    main()
