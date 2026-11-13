"""Revalidate the final composed project, rather than trusting intermediate CECs."""
import argparse
from collections import Counter
import json
from pathlib import Path
from gowin_cones import modules,parse,readback,supports
from gowin_blif import read_blif
from run_abc_reproduction import ROOT,cec,sha


def skeleton(text,changed):
    index=modules(text);parts=[];previous=0
    for name,(start,end) in index.items():
        if name not in changed:continue
        parts.extend([text[previous:start],f'\n<CHANGED_MODULE:{name}>\n']);previous=end
    parts.append(text[previous:]);return ''.join(parts)


def check_statements(old,new):
    removed={s.start for s in old['removed']}
    retained=Counter(old['body'][s.start:s.end] for s in old['statements'] if s.start not in removed)
    actual=Counter(new['body'][s.start:s.end] for s in new['statements'])
    if retained-actual:raise ValueError('Original non-LUT statement changed or disappeared')
    new_logic={s.start for s in new['removed']}
    old_cells={s.name for s in old['opaque']}
    # Extra cells may be generated LUTs or explicit constant drivers only.
    for s in new['opaque']:
        if s.name not in old_cells and s.kind not in ('VCC','GND'):
            raise ValueError('Replacement introduced an opaque state/hard cell')
    extra=actual-retained
    allowed=Counter(new['body'][s.start:s.end] for s in new['statements']
                    if s.start in new_logic or s.kind=='wire' or
                    (s.pins is not None and s.name not in old_cells and s.kind in ('VCC','GND')))
    if extra-allowed:raise ValueError('Unexpected new structural statement')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('original',type=Path)
    ap.add_argument('candidate',type=Path);ap.add_argument('--manifest',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    manifest=json.loads(args.manifest.read_text())
    if sha(args.original)!=manifest['input_sha256'] or sha(args.candidate)!=manifest['output_sha256']:
        raise ValueError('Final/original artifact hash mismatch')
    if manifest['status'] not in ('complete','complete_with_failures'):raise ValueError('Transformation not terminal')
    original=args.original.read_bytes().decode('utf-8');candidate=args.candidate.read_bytes().decode('utf-8')
    old_modules=modules(original);new_modules=modules(candidate)
    changed={r['name'] for r in manifest['modules'] if r['chosen']!='seed'}
    if list(old_modules)!=list(new_modules):raise ValueError('Project module interfaces/order changed')
    if skeleton(original,changed)!=skeleton(candidate,changed):raise ValueError('Source outside selected modules changed')
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    result=dict(scope='Final composed netlist cut-boundary CEC and structural preservation. Not RTL/board acceptance.',
        original_sha256=sha(args.original),candidate_sha256=sha(args.candidate),
        project_module_count=len(old_modules),unchanged_module_count=len(old_modules)-len(changed),
        outside_changed_modules_byte_equal=True,checks=[],status='running')
    def save():(out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    save()
    for name in sorted(changed):
        old=parse(original,name);new=parse(candidate,name);check_statements(old,new)
        folder=out/name;folder.mkdir();source=folder/'source.blif';back=folder/'readback.blif'
        source.write_text(old['blif']);back.write_text(readback(new,old))
        before,after=supports(read_blif(source)),supports(read_blif(back))
        if any(after[p]-before[p] for p in after):raise ValueError('New dependency in final composed project')
        proof=cec(ROOT/'build/abc/_TEST/abc.exe',source,back,folder/'cec.log',180)
        result['checks'].append(dict(name=name,non_lut_statements_preserved=True,no_new_boundary_dependencies=True,
            original_luts=len(old['luts']),replacement_luts=len(new['luts']),cec=proof));save()
        if not proof['equivalent']:raise RuntimeError('Final-project boundary CEC unproven: '+name)
    result['status']='complete';save();print(json.dumps({k:v for k,v in result.items() if k!='checks'},indent=2))


if __name__=='__main__':main()
