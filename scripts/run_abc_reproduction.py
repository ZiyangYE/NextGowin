"""Reproduce R1's ABC mapping sequences with pinned inputs and explicit CEC.

Paper-model experiments use unit LUT delays. Gowin's physical implementation
and externally constrained mapping are separate experiments.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
ABC_SOURCE = ROOT/'third_party/abc'
EPFL = ROOT/'third_party/epfl'
PREPROCESS = 'dfraig; resyn; resyn2; resyn2rs; if -y -K 6; resyn2rs; strash'
SCRIPTS = {
    'baseline': 'dch; if -K {k}',
    'acd': 'dch; if -Z {k} -K {cut}',
    'acd_remap': 'dch; if -Z {k} -K {cut}; strash; dch -f; if -K {k}',
    'sat_cascade': 'dch; if -s -S {k}{k} -K {cut}',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def network_sha(path):
    # ABC writes wall-clock timestamps in comments. They do not change the
    # network; ignore whole comment/empty lines when reusing an existing proof.
    lines=[line.strip() for line in path.read_text().splitlines()
           if line.strip() and not line.lstrip().startswith('#')]
    return hashlib.sha256(('\n'.join(lines)+'\n').encode()).hexdigest()


def git_commit(path):
    # Offline handoffs retain source snapshots and revision metadata, not .git.
    revisions=ROOT/'handoff/source_revisions.json'
    if not (path/'.git').exists() and revisions.exists():
        key=path.resolve().relative_to(ROOT).as_posix()
        record=json.loads(revisions.read_text()).get(key)
        if record:return record['head']
    return subprocess.check_output(['git','-c',f'safe.directory={path.as_posix()}',
        '-C',str(path),'rev-parse','HEAD'],text=True).strip()


def invoke(binary, commands, logfile, timeout):
    from flow_budget import clipped
    timeout=clipped(timeout)
    started = time.perf_counter()
    try:
        cp = subprocess.run([str(binary),'-c',commands],cwd=ABC_SOURCE,
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace',timeout=timeout)
        text, rc = cp.stdout, cp.returncode
        errors = ['LMS manager is not running','unknown command','Error:','Error in','Assertion failed']
        state = 'ok' if rc==0 and 'NEXTGOWIN_DONE' in [line.strip() for line in text.splitlines()] and not any(e in text for e in errors) else 'error'
    except subprocess.TimeoutExpired as exc:
        text = exc.stdout or b''
        if isinstance(text,bytes):
            text = text.decode(errors='replace')
        rc, state = None, 'timeout'
    logfile.write_text(text,encoding='utf8')
    return dict(status=state,returncode=rc,wall_seconds=time.perf_counter()-started,
                command=commands,log=str(logfile)),text


def cec(binary, original, mapped, logfile, timeout):
    reference_hash,candidate_hash=sha(original),sha(mapped)
    result,text = invoke(binary,f'cec -n "{original.as_posix()}" "{mapped.as_posix()}"; echo NEXTGOWIN_DONE',logfile,timeout)
    unchanged=reference_hash==sha(original) and candidate_hash==sha(mapped)
    result.update(reference_sha256=reference_hash,candidate_sha256=candidate_hash,inputs_unchanged=unchanged)
    result['equivalent'] = result['status']=='ok' and 'Networks are equivalent' in text and unchanged
    if not result['equivalent'] and result['status']=='ok':
        result['status'] = 'unproven'
    return result


def stats(text):
    text = re.sub(r'\x1b\[[0-9;]*m','',text)
    matches = re.findall(r'nd\s*=\s*(\d+).*?edge\s*=\s*(\d+).*?lev\s*=\s*(\d+)',text)
    timing = re.findall(r'elapse:\s*([\d.]+) seconds',text)
    if not matches or not timing:
        raise ValueError('Missing mapped LUT statistics or mapping CPU time')
    lut,edges,depth = map(int,matches[-1])
    return dict(luts=lut,edges=edges,depth=depth,mapping_cpu_seconds=float(timing[-1]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--binary',type=Path,default=ROOT/'build/abc/_TEST/abc.exe')
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--bench',nargs='+',help='Default: all 20 EPFL arithmetic/control inputs')
    ap.add_argument('--configs',nargs='+',choices=list(SCRIPTS),default=list(SCRIPTS))
    ap.add_argument('--lut',type=int,choices=[4,6],default=6)
    ap.add_argument('--repeats',type=int,default=5)
    ap.add_argument('--timeout',type=int,default=300)
    ap.add_argument('--resume',action='store_true')
    args = ap.parse_args()
    if args.repeats<1:
        ap.error('--repeats must be positive')
    binary, out = args.binary.resolve(),args.out.resolve()
    out.mkdir(parents=True,exist_ok=args.resume)
    inputs = {p.stem:p for folder in ['arithmetic','random_control'] for p in (EPFL/folder).glob('*.aig')}
    selected = args.bench or sorted(inputs)
    if set(selected)-inputs.keys():
        ap.error('Unknown benchmark names')
    result = dict(schema='abc-r1-reproduction-v1',abc_commit=git_commit(ABC_SOURCE),
        epfl_commit=git_commit(EPFL),binary_sha256=sha(binary),lut_size=args.lut,
        repeats=args.repeats,timeout_seconds=args.timeout,preprocess=PREPROCESS,
        scope='Current pinned upstream implementation of R1 sequences; unit-delay model, not historical binary or routed timing',
        runs=[],status='running')
    seed = EPFL/'random_control/ctrl.aig'
    result['library_initialization'] = 'Read fixed ctrl seed, &get; &if -y -K 6 to load current upstream built-in LMS; then read fresh experiment input'
    result['library_difference'] = 'Historical paper LMS artifact was not supplied; current upstream built-in library is used and results are not claimed numerically identical to the paper'
    if args.resume and (out/'manifest.json').exists():
        previous=json.loads((out/'manifest.json').read_text())
        for key in ['abc_commit','epfl_commit','binary_sha256','lut_size','repeats','preprocess']:
            if previous[key]!=result[key]:
                raise ValueError(f'Cannot resume a different experiment: {key}')
        result=previous
        result['status']='running'
    result['cec_reuse_policy']='Only reuse a positive CEC proof when complete non-comment BLIF text is identical; retain raw file SHA256 separately'
    def save():
        (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    save()
    for name in selected:
        folder = out/name
        folder.mkdir(exist_ok=args.resume)
        normalized = folder/'normalized.aig'
        item=next((r for r in result['runs'] if r['benchmark']==name),None)
        if item:
            if item['input_sha256']!=sha(inputs[name]):
                raise ValueError('Benchmark input changed')
            if item.get('status')=='complete':
                if not normalized.exists() or sha(normalized)!=item.get('normalized_sha256'):
                    raise ValueError('Completed checkpoint normalized input missing or changed')
                for label,config in item['configs'].items():
                    for rep,sample in enumerate(config['samples']):
                        artifact=folder/f'{label}_{rep}.blif'
                        proof=config['cec_by_sha256'].get(sample.get('mapped_sha256'),{})
                        log=Path(proof.get('log','missing'))
                        if (not artifact.exists() or sha(artifact)!=sample.get('mapped_sha256') or
                            not proof.get('equivalent') or not log.exists() or
                            'Networks are equivalent' not in log.read_text(errors='replace')):
                            raise ValueError('Completed checkpoint artifacts/proof missing or changed')
                continue
        else:
            item = dict(benchmark=name,input=str(inputs[name]),input_sha256=sha(inputs[name]),configs={})
            result['runs'].append(item)
        command = f'read "{seed.as_posix()}"; &get; &if -y -K 6; read "{inputs[name].as_posix()}"; {PREPROCESS}; write_aiger "{normalized.as_posix()}"; echo NEXTGOWIN_DONE'
        if not (item.get('preprocess',{}).get('status')=='ok' and normalized.exists()
                and item.get('normalized_sha256')==sha(normalized)):
            item['preprocess'],_ = invoke(binary,command,folder/'preprocess.log',args.timeout)
        if item['preprocess']['status']!='ok' or not normalized.exists():
            item['status']='preprocess_failed'
            save()
            print(name,'preprocess failed',flush=True)
            continue
        item['normalized_sha256'] = sha(normalized)
        if not item.get('preprocess_cec',{}).get('equivalent'):
            item['preprocess_cec'] = cec(binary,inputs[name],normalized,folder/'preprocess_cec.log',args.timeout)
        if not item['preprocess_cec']['equivalent']:
            item['status']='preprocess_unproven'
            save()
            print(name,'preprocess CEC unproven',flush=True)
            continue
        for label in args.configs:
            script = SCRIPTS[label].format(k=args.lut,cut=args.lut+2)
            if name=='hyp':
                # R1 explicitly omits structural choices for hyp because of an
                # ABC issue; do not silently apply the general dch recipe.
                script=script.replace('dch -f; ','').replace('dch; ','')
            config = item['configs'].get(label,dict(script=script,samples=[],cec_by_sha256={}))
            if config['script']!=script:
                raise ValueError('Mapping configuration changed')
            if config.get('valid'):
                continue
            config.setdefault('cec_by_network_sha256',{})
            config['proof_policy']='A mapped CEC may use the already-proven normalized AIG as reference; original -> normalized -> mapped is a transitive equivalence proof'
            item['configs'][label] = config
            for rep in range(args.repeats):
                mapped = folder/f'{label}_{rep}.blif'
                if rep<len(config['samples']):
                    sample=config['samples'][rep]
                    if sample.get('equivalent') and sample['status']=='ok' and mapped.exists() and sha(mapped)==sample['mapped_sha256']:
                        canonical=network_sha(mapped)
                        sample['network_sha256']=canonical
                        proof=config['cec_by_sha256'].get(sample['mapped_sha256'])
                        if proof and proof['equivalent']:
                            config['cec_by_network_sha256'][canonical]=proof
                        continue
                    config['samples']=config['samples'][:rep]
                command = f'read "{normalized.as_posix()}"; time -c; {script}; time -p; print_stats; write_blif "{mapped.as_posix()}"; echo NEXTGOWIN_DONE'
                sample,text = invoke(binary,command,folder/f'{label}_{rep}.log',args.timeout)
                config['samples'].append(sample)
                if sample['status']!='ok' or not mapped.exists():
                    sample['status'] = 'mapping_failed' if sample['status']=='ok' else sample['status']
                    break
                try:
                    sample.update(stats(text))
                except ValueError as exc:
                    sample['status'],sample['error'] = 'invalid_stats',str(exc)
                    break
                digest = sample['mapped_sha256'] = sha(mapped)
                canonical=sample['network_sha256']=network_sha(mapped)
                if canonical not in config['cec_by_network_sha256']:
                    config['cec_by_network_sha256'][canonical] = cec(binary,normalized,mapped,folder/f'{label}_{rep}_cec.log',args.timeout)
                proof=config['cec_by_network_sha256'][canonical]
                config['cec_by_sha256'][digest]=proof
                sample['equivalent'] = proof['equivalent']
                if not sample['equivalent']:
                    sample['status']='cec_unproven'
                    break
                save()
            valid = len(config['samples'])==args.repeats and all(s.get('equivalent') and s['status']=='ok' for s in config['samples'])
            config['valid'] = valid
            if valid:
                config['summary'] = {key:statistics.median(s[key] for s in config['samples']) for key in
                    ['luts','edges','depth','mapping_cpu_seconds','wall_seconds']}
            save()
            print(name,label,config.get('summary',config['samples'][-1]['status']),flush=True)
        item['status']='complete' if all(c['valid'] for c in item['configs'].values()) else 'incomplete'
        save()
    result['status']='complete' if all(r.get('status')=='complete' for r in result['runs']) else 'completed_with_failures'
    save()


if __name__=='__main__':
    main()
