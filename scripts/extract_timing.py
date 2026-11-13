"""Extract observed cell arcs from Gowin textual STA reports, with provenance.

These samples are not a complete timing library. tNET is never a cell arc;
setup/hold signed required-path adjustments are retained separately.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import statistics
from ownpr_image_storage import read_text as read_artifact_text,sha as artifact_sha

NUMBER = r'[-+]?\d+(?:\.\d+)?'
ROW = re.compile(rf'^\s*({NUMBER})\s+({NUMBER})\s+(t\w+)\s+(?:(RR|RF|FR|FF)\s+)?(\d+)\s+(\S+)\s+(\S+)\s*$')
CELL = re.compile(r'^\s*(LUT[1-6]|DFF\w*|MUX\w*|ALU\w*|IBUF\w*|OBUF\w*|DPB\w*|SP\w*)\s+(\\\S+|[\w/.$]+)\s*\(', re.M)
CONSTRAINT_KINDS = ('tSu','tH','tHld','tRec','tRem')


def extract(report, netlist):
    text = read_artifact_text(report,errors='replace')
    cells = {name.lstrip('\\'): kind for kind, name in CELL.findall(netlist.read_text(errors='replace'))}
    metadata = dict(re.findall(r'^<([^>]+)>:\s*(.*?)\s*$', text, re.M))
    if not metadata.get('Part Number') or not metadata.get('Setup Delay Model'):
        raise ValueError(f'{report}: missing device/corner metadata')
    arcs = []
    analysis = section = None
    previous = None
    arrival_endpoint = None
    path_id = None
    row_count = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        heading = re.match(r'^3\.(?:3|7)\.\d+ (Setup|Hold|Recovery|Removal) Analysis Report', line)
        if heading:
            analysis = heading[1].lower()
            section = None
        pm = re.fullmatch(r'\s*Path(\d+)\s*', line)
        if pm:
            path_id = int(pm[1])
            previous = None
            arrival_endpoint = None
        if line.startswith('Data Arrival Path:'):
            section, previous = 'arrival', None
        elif line.startswith('Data Required Path:'):
            section, previous = 'required', None
        elif line.startswith('Path Statistics:'):
            section, previous = None, None
        m = ROW.match(line) if section and analysis else None
        if not m:
            continue
        row_count += 1
        at, delay, kind, rf, fanout, loc, node = m.groups()
        if section=='arrival':
            arrival_endpoint=node
        instance, sep, pin = node.rpartition('/')
        cell_type = cells.get(instance)
        # Required-path setup/hold rows name the instance, not a pin.
        if kind in CONSTRAINT_KINDS:
            instance, pin, cell_type = node, kind, cells.get(node)
        if kind in ('tINS', 'tC2Q') and cell_type and previous:
            prev_instance, prev_pin = previous['node'].rsplit('/', 1) if '/' in previous['node'] else ('', '')
            if prev_instance == instance:
                arcs.append(dict(cell=cell_type, instance=instance, from_pin=prev_pin,
                    to_pin=pin, delay_ns=float(delay), kind=kind, transition=rf,
                    fanout=int(fanout), location=loc, analysis=analysis, section=section,
                    path=path_id, line=lineno))
        elif kind in CONSTRAINT_KINDS and cell_type:
            endpoint_instance,_,endpoint_pin=(arrival_endpoint or '').rpartition('/')
            arcs.append(dict(cell=cell_type, instance=instance, from_pin=None,
                to_pin=pin, delay_ns=float(delay), kind=kind, transition=rf,
                constrained_pin=endpoint_pin if endpoint_instance==instance else None,
                fanout=int(fanout), location=loc, analysis=analysis, section=section,
                path=path_id, line=lineno))
        previous = {'node': node, 'kind': kind}
    if not arcs:
        raise ValueError(f'{report}: no recognized arcs; refusing empty success')
    return dict(metadata=metadata, arcs=arcs, parsed_rows=row_count,
        report=str(report), netlist=str(netlist),
        report_sha256=artifact_sha(report),
        netlist_sha256=hashlib.sha256(netlist.read_bytes()).hexdigest())


def summarize(sources):
    groups = defaultdict(dict)
    for source in sources:
        for a in source['arcs']:
            corner = source['metadata']['Hold Delay Model' if a['analysis'] in ('hold', 'removal') else 'Setup Delay Model']
            key = (source['metadata']['Part Number'], corner, a['cell'], a['from_pin'],
                   a['to_pin'], a['kind'], a['transition'])
            # Repeated appearance of one physical arc in different paths is one observation.
            ident = (source['report_sha256'], a['instance'], a['from_pin'], a['to_pin'], a['delay_ns'])
            groups[key][ident] = a['delay_ns']
    result = []
    for key, values in sorted(groups.items(), key=lambda x: str(x[0])):
        part, corner, cell, ip, op, kind, rf = key
        vals = list(values.values())
        result.append(dict(part=part, corner=corner, cell=cell, from_pin=ip,
            to_pin=op, kind=kind, transition=rf, observations=len(vals),
            min_ns=min(vals), median_ns=statistics.median(vals), max_ns=max(vals)))
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pair', nargs=2, action='append', required=True, metavar=('REPORT', 'NETLIST'))
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    sources = [extract(Path(r), Path(n)) for r, n in args.pair]
    output = dict(schema='gowin-observed-arcs-v1', units='ns',
        scope='Observed paths only; not an exhaustive library or signoff model',
        constraint_semantics='tSu/tH/tHld values are signed report adjustments, not normalized constraints',
        sources=sources, summary=summarize(sources))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2)+'\n')
    print(f'{len(sources)} sources, {sum(len(s["arcs"]) for s in sources)} arc rows, {len(output["summary"])} groups')


if __name__ == '__main__':
    main()
