"""Restore explicit semantics in Gowin's readable Verific Verilog export.

The export stores initial values and primitive specialization parameters in
comments. They must not silently become empty modules in the next compiler.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from gowin_cones import IDENT, TOKEN, masked
from restore_verific_memories import restore


def units(text):
    # Quoted parameter values may be part of a Verilog escaped identifier.
    # Replacing their contents would collapse distinct specialized module names.
    clean = masked(text)
    result = {}
    for match in re.finditer(r'^module\s+('+IDENT+r')\s*', clean, re.M):
        end = re.search(r'\bendmodule\b', clean[match.end():])
        if end is None or match[1] in result:
            raise ValueError('Missing endmodule or duplicate definition')
        result[match[1]] = (match.start(), match.end()+end.end())
    if not result:
        raise ValueError('No exported module definitions')
    return result


def convert(text):
    primitive_specs = {}
    libraries = {}
    remove = []
    for name, (a, b) in units(text).items():
        body = text[a:b]
        header = body[:body.index('\n', body.index(';'))]
        # Only installed primitive stubs qualify. Protected IP bodies remain.
        if 'prim_syn.v' not in header:
            continue
        match = re.search(r'\bcell_name=([A-Za-z_][\w$]*)', header)
        if not match:
            raise ValueError('Primitive stub without original cell type: '+name)
        base = match[1]
        params = dict(re.findall(r'^\s*// parameter (\w+)\s*=\s*(.*?)\s*;\s*$', body, re.M))
        # ID is an annotation, not a hardware configuration. Keep its value in
        # provenance; do not pass unsupported ID parameters to Yosys's library.
        actual = {p: v for p, v in params.items() if p != 'ID'}
        clean = masked(body)
        declarations = re.findall(r'^\s*(?:input|output|inout)\b[^;]*;', clean, re.M)
        prefix = re.match(r'module\s+'+IDENT+r'\s*', clean)
        port_header = re.search(r'\(.*?\)', clean[prefix.end():clean.index(';')], re.S)
        if not port_header or not declarations:
            raise ValueError('Primitive interface was not recoverable: '+name)
        interface = (port_header[0], declarations)
        signature = TOKEN.findall(port_header[0]+'\n'+'\n'.join(declarations))
        if base in libraries and libraries[base]['signature'] != signature:
            raise ValueError('Primitive specializations disagree on port schema: '+base)
        libraries.setdefault(base, dict(interface=interface, parameters=actual, signature=signature))
        primitive_specs[name] = dict(cell=base, parameters=actual, vendor_id=params.get('ID'),
            stub_sha256=hashlib.sha256(body.encode()).hexdigest())
        remove.append((a, b))
    chunks = []
    cursor = 0
    for a, b in remove:
        chunks.append(text[cursor:a])
        cursor = b
    chunks.append(text[cursor:])
    text = ''.join(chunks)
    text, memories = restore(text, units)
    replacements = dict.fromkeys(primitive_specs, 0)
    def specialize(match):
        name = match[2]
        if name not in primitive_specs:
            return match[0]
        spec = primitive_specs[name]
        parameters = ', '.join('.'+key+'('+value+')' for key, value in spec['parameters'].items())
        replacements[name] += 1
        return match[1]+spec['cell']+(' #('+parameters+')' if parameters else '')
    text = re.sub(r'(^[ \t]*)('+IDENT+r')(?=\s+'+IDENT+r'\s*\()', specialize, text, flags=re.M)
    initial_values = []
    count_init = len(re.findall(r'/\* Init Val:', text))
    # One declaration per initialized exported net; escaped identifiers and
    # vector ranges are preserved verbatim. Yosys understands wire init attrs.
    pattern = re.compile(r'^([ \t]*)(wire|reg|output)\b([^;\n]*?)/\* Init Val:\s*([01xXzZ]+)\s*\*/', re.M)
    def initial(match):
        value = match[4].lower()
        initial_values.append(dict(declaration=(match[2]+match[3]).strip(), bits=value))
        return match[1]+f"(* init = {len(value)}'b{value} *) "+match[2]+match[3]
    text = pattern.sub(initial, text)
    if len(initial_values) != count_init:
        raise ValueError('Untranslated initial-value annotation')
    kept = 0
    def preserve(match):
        nonlocal kept
        line = match[0]
        if re.search(r'\bsyn_(?:keep|preserve)\s*=\s*1\b', line):
            kept += 1
            return match[1]+'(* keep = 1 *) '+line[len(match[1]):]
        return line
    text = re.sub(r'^([ \t]*)(?:\(\*[^\n]*?\*\)\s*)?(?:wire|reg|input|output)\b[^\n]*', preserve, text, flags=re.M)
    # Explicit vendor black boxes must never be optimized as empty logic.
    text, blackboxes = re.subn(r'(^module\s+[^\n]*?)/\* verific ([^\n]*?\bsyn_black_box=1\b[^\n]*?)\*/',
        r'(* blackbox = 1 *) \1/* verific \2*/', text, flags=re.M)
    if re.search(r'^\s*module\s+\\(?:LUT\d|PLL|SDP|DPB)\(', masked(text), re.M):
        raise ValueError('Primitive specializations remain unresolved')
    library = []
    for base, spec in libraries.items():
        library.append('(* blackbox = 1 *) module '+base+' '+spec['interface'][0]+';')
        library.extend('parameter '+p+' = '+v+';' for p, v in spec['parameters'].items())
        library.extend(spec['interface'][1])
        library.append('endmodule')
    return text, dict(primitive_specializations=primitive_specs, rewritten_instances=replacements,
        vendor_library_verilog='\n'.join(library)+'\n',
        restored_memories=memories,
        initial_values=initial_values, kept_declarations=kept, explicit_blackboxes=blackboxes,
        scope='Restores exported annotations. Primitive ID tags recorded separately; no IP decryption or RTL semantics inferred from names.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('input', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = args.input.read_text(encoding='utf-8')
    text, manifest = convert(source)
    target = out/'design.v'
    target.write_text(text, encoding='utf-8')
    library = out/'vendor_cells.v'
    library.write_text(manifest.pop('vendor_library_verilog'), encoding='utf-8')
    manifest.update(source_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
                    output_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                    vendor_library_sha256=hashlib.sha256(library.read_bytes()).hexdigest())
    (out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(dict(primitives=len(manifest['primitive_specializations']),
        initial_values=len(manifest['initial_values']), kept=manifest['kept_declarations']), indent=2))


if __name__ == '__main__':
    main()
