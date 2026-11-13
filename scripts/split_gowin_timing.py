"""Split the installed GW5AST timing database into auditable source data.

Legacy names come from Apicula; extended table structure follows the installed
DLL reader. Numeric extended group/arc IDs are preserved until verified.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]


class Reader:
    def __init__(self, data, offset):
        self.data, self.pos = data, offset

    def read(self, fmt):
        size = struct.calcsize('<'+fmt)
        if self.pos+size>len(self.data):
            raise ValueError(f'truncated table at {self.pos:#x}')
        result = struct.unpack_from('<'+fmt, self.data, self.pos)
        self.pos += size
        return result[0] if len(result)==1 else list(result)

    def marker(self, expected):
        offset = self.pos
        actual = self.read('I')
        if actual != expected:
            raise ValueError(f'marker at {offset:#x}: expected {expected}, got {actual}')


def extended(data):
    reader = Reader(data, 3*15552)
    prefix = reader.data[reader.pos:reader.pos+15].hex()
    reader.pos += 15  # DLL: fread(4,1), fread(1,11), then section marker.
    reader.marker(55)
    id_count, group_count, extra_width = [reader.read('H') for _ in range(3)]
    flags = reader.read('I')
    ids = [reader.read('h') for _ in range(id_count)]
    groups = []
    for index in range(group_count):
        pin_count, row_count = reader.read('H'), reader.read('H')
        pins = [reader.read('h') for _ in range(pin_count)]
        rows = []
        for row in range(row_count):
            offset = reader.pos
            count = reader.read('H')
            if count < 12 or count > 512:
                raise ValueError(f'invalid float count {count} at {offset:#x}, group {index}, row {row}, rows {row_count}, pins {pin_count}')
            values = reader.read(f'{count}f')
            rows.append(dict(index=row, byte_offset=offset, float_count=count, values=values))
        group = dict(index=index, pin_ids=pins, rows=rows)
        if index == ids[6]:
            map_width, map_rows = reader.read('H'), reader.read('H')
            map_ids = [reader.read('h') for _ in range(map_width)]
            matrix = [reader.read(f'{flags+1}h') for _ in range(map_rows)]
            group['extra'] = dict(ids=map_ids, rows=matrix)
        groups.append(group)
    reader.marker(55)
    relations = {}
    for marker in (56, 57):
        reader.marker(marker)
        mapping_count, table_count = reader.read('H'), reader.read('H')
        mapping = [reader.read('h') for _ in range(mapping_count)]
        tables = []
        for table_index in range(table_count):
            records = []
            for _ in range(reader.read('H')):
                offset = reader.pos
                source_id, target_id = reader.read('H'), reader.read('H')
                attributes = [reader.read('B') for _ in range(3)]
                count = reader.read('H')
                if count > 512:
                    raise ValueError(f'invalid relation count at {offset:#x}')
                values = [reader.read('f') for _ in range(count)]
                records.append(dict(byte_offset=offset, source_id=source_id,
                    target_id=target_id, attributes=attributes, values=values))
            tables.append(dict(index=table_index, records=records))
        reader.marker(marker)
        relations[str(marker)] = dict(mapping=mapping,tables=tables)
    return dict(prefix_hex=prefix, id_map=ids, flags=flags, extra_width=extra_width,
                groups=groups, relations=relations, consumed_through=reader.pos,
                remaining_bytes=len(data)-reader.pos)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tm', type=Path, default=Path('C:/Gowin/Gowin_V1.9.12.04_x64/IDE/share/device/GW5AST-138C/GW5AST-138C.tm'))
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    data = args.tm.read_bytes()
    spec = importlib.util.spec_from_file_location('apicula_tm', ROOT/'third_party/apicula/tm_parser.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with args.tm.open('rb') as stream:
        legacy = module.read_tm(stream, 'GW5AST-138C')
    output = dict(schema='gowin-tm-split-v1', source=str(args.tm),
        sha256=hashlib.sha256(data).hexdigest(), file_bytes=len(data),
        legacy=legacy, extended_55=extended(data),
        validation='Structural extraction only; extended numeric IDs and corner slots not yet semantically mapped')
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/'timing.json').write_text(json.dumps(output,indent=2)+'\n')
    end = output['extended_55']['consumed_through']
    (args.out/'unparsed_tail.bin').write_bytes(data[end:])
    for group in output['extended_55']['groups']:
        (args.out/f'extended_group_{group["index"]}.json').write_text(json.dumps(group,indent=2)+'\n')
    for marker, relations in output['extended_55']['relations'].items():
        (args.out/f'extended_relations_{marker}.json').write_text(json.dumps(relations,indent=2)+'\n')
    provenance = dict(database_sha256=output['sha256'], parser_source='https://github.com/YosysHQ/apicula/blob/master/apycula/tm_parser.py',
        parser_sha256=hashlib.sha256((ROOT/'third_party/apicula/tm_parser.py').read_bytes()).hexdigest(),
        license='third_party/apicula/LICENSE', legacy_bytes=3*15552,
        extended_end=end, unparsed_bytes=len(data)-end,
        group_rows=sum(len(g['rows']) for g in output['extended_55']['groups']),
        relation_records={k:sum(len(t['records']) for t in v['tables']) for k,v in output['extended_55']['relations'].items()})
    (args.out/'manifest.json').write_text(json.dumps(provenance,indent=2)+'\n')
    for speed, sections in legacy.items():
        for name, values in sections.items():
            (args.out/f'legacy_{speed.replace("/", "_")}_{name}.json').write_text(json.dumps(values,indent=2)+'\n')
    print(json.dumps({k:v for k,v in output.items() if k not in ['legacy','extended_55']},indent=2))
    print('extended groups',len(output['extended_55']['groups']), 'remaining bytes',output['extended_55']['remaining_bytes'])


if __name__ == '__main__':
    main()
