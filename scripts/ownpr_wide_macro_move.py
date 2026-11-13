"""Timing-guided moves of exclusive calibrated wide-LUT macros.

Distance-scaled observed wire delay is only a candidate-ranking heuristic.
Logical cells, FF locations, fixed pads and the clock tree stay unchanged.
"""
import argparse, copy, json, shutil
from pathlib import Path
from ownpr_mux_macros import tree, validate
from ownpr_place import sha


def diversify(candidates):
    """Try different macros before neighboring sites of the same macro."""
    ranks = {}; tagged = []
    for candidate in candidates:
        root = candidate['root']; rank = ranks.get(root, 0); ranks[root] = rank + 1
        tagged.append((candidate['worst_slack_ns'], rank, -candidate['estimated_local_gain_ns'], candidate['key'], candidate))
    return [row[-1] for row in sorted(tagged, key=lambda row: row[:-1])]


def relocate(design, placed, device, root, row, column):
    validate(design, placed, device)
    result = copy.deepcopy(placed)
    macro = next((m for m in result.get('wide_macros', []) if m['root'] == root), None)
    if macro is None:
        raise ValueError('Unknown wide macro')
    old = macro['row'], macro['column']
    if (row, column) == old:
        raise ValueError('Macro move does not change its site')
    spec = tree(design, root, macro['root_slot'])
    for name in spec['leaves']:
        result['clusters'][name].update(row=row, column=column)
    for name in spec['muxes']:
        result['mux_clusters'][name].update(row=row, column=column)
    macro.update(row=row, column=column)
    audit = validate(design, result, device)
    result.update(wide_macro_placement=audit, physical_legality_complete=False, timing_signoff=False)
    return result, dict(root=root, old_cfu=list(old), new_cfu=[row, column],
        moved_luts=sorted(spec['leaves']), moved_muxes=sorted(spec['muxes']),
        logical_netlist_byte_identical=True, fixed_other_cells=True)


def proposals(design, placed, device, paths, endpoints, radius=4, per_macro=3):
    cells = design['modules']['top']['cells']
    drivers = {bits[0]: (n, pin) for n, cell in cells.items() for pin, bits in cell['connections'].items()
               if cell['port_directions'][pin] == 'output'}
    points = {(p['instance'], p['pin']): p['nodes'][0]['node'][:2] for p in endpoints if p['nodes']}
    used = {(p['row'], p['column']) for category in ('clusters', 'arithmetic_clusters', 'mux_clusters')
            for p in placed.get(category, {}).values()}
    output = []
    distance = lambda a, b: abs(a[0] - b[0]) + abs(a[1] - b[1])
    for macro in placed.get('wide_macros', []):
        root = macro['root']; spec = tree(design, root, macro['root_slot'])
        members = set(spec['leaves']) | set(spec['muxes'])
        witnesses = []
        for path in paths:
            arcs = []
            for arc in path['arrival']:
                n, pin = arc['instance'], arc['pin']; cell = cells[n]
                if arc['kind'] != 'tNET' or pin == 'CLK' or cell['port_directions'].get(pin) != 'input':
                    continue
                source = drivers.get(cell['connections'][pin][0]); destination = n, pin
                if source is None or source not in points or destination not in points:
                    continue
                if (source[0] in members) == (n in members):
                    continue
                arcs.append((points[source], points[destination], source[0] in members, arc['delay_ns']))
            if arcs:
                witnesses.append((path['path'], path['slack_ns'], arcs))
        if not witnesses:
            continue
        worst = min(p[1] for p in witnesses)
        options = []
        r0, c0 = macro['row'], macro['column']
        for r in range(max(0, r0-radius), min(len(device.grid), r0+radius+1)):
            for c in range(max(0, c0-radius), min(len(device.grid[r]), c0+radius+1)):
                if device.grid[r][c] != 17 or (r, c) in used:
                    continue
                gains = []
                for pid, slack, arcs in witnesses:
                    gain = 0.
                    for source, dest, move_source, delay in arcs:
                        moved = source if move_source else dest
                        target = [moved[0]+r-r0, moved[1]+c-c0]
                        old = distance(source, dest)
                        new = distance(target, dest) if move_source else distance(source, target)
                        gain += delay * (old-new) / max(old, 1)
                    gains.append(dict(path=pid, slack_ns=slack, estimated_wire_gain_ns=gain))
                worst_gain = min(g['estimated_wire_gain_ns'] for g in gains if g['slack_ns'] == worst)
                if worst_gain <= .001:
                    continue
                options.append(dict(kind='wide_move', key=f'wide_move:{root}:{r}:{c}', root=root,
                    row=r, column=c, old_cfu=[r0,c0], lut_growth=0, worst_slack_ns=worst,
                    estimated_local_gain_ns=round(worst_gain,6), witness_paths=[g['path'] for g in gains],
                    path_estimates=gains, ranking_model='distance-scaled observed wires, not final timing'))
        options.sort(key=lambda x: (-x['estimated_local_gain_ns'], x['row'], x['column']))
        output.extend(options[:per_macro])
    return sorted(output, key=lambda x: (x['worst_slack_ns'], -x['estimated_local_gain_ns'], x['key']))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', type=Path, required=True); p.add_argument('--root', required=True)
    p.add_argument('--row', type=int, required=True); p.add_argument('--column', type=int, required=True)
    p.add_argument('--out', type=Path, required=True); a = p.parse_args()
    from ownpr_device import Device, ROOT
    from workspace_budget import reserve
    reserve(ROOT, 10_000_000)
    design = json.loads((a.base/'packed.json').read_text()); placed = json.loads((a.base/'placement.json').read_text())
    changed, info = relocate(design, placed, Device(), a.root, a.row, a.column)
    a.out.mkdir(exist_ok=False); shutil.copyfile(a.base/'packed.json', a.out/'packed.json')
    (a.out/'placement.json').write_text(json.dumps(changed, indent=2))
    meta = json.loads((a.base/'manifest.json').read_text())
    meta.update(status='placed', netlist_sha256=sha(a.out/'packed.json'), placement_sha256=sha(a.out/'placement.json'),
        proof=[], macro_move=info, source_placement_sha256=sha(a.base/'placement.json'),
        timing_signoff=False, physical_legality_complete=False)
    (a.out/'manifest.json').write_text(json.dumps(meta, indent=2)); print(json.dumps(info))


if __name__ == '__main__': main()
