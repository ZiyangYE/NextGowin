"""Replay native node-delay samples; report comparison remains diagnostic.

This does not infer a general wire model or sign off a design. In particular,
register input mode delays can be absent from a physical parent-chain sum.
"""
import argparse,hashlib,json,math,re,struct
from collections import defaultdict
from pathlib import Path
from capture_runtime_tables import EXPECTED


def f32(value):
    return struct.unpack('<f',struct.pack('<f',value))[0]


def replay(data):
    nodes={tuple(row['node']):row for row in data['nodes']}
    if len(nodes)!=len(data['nodes']):raise ValueError('Duplicate native node')
    active=data['active_selector']
    if active not in range(3):raise ValueError('Unknown selector')
    for row in nodes.values():
        vectors=row['values_by_selector']
        if len(vectors)!=3 or any(len(v)!=4 or not all(math.isfinite(x) for x in v) for v in vectors):
            raise ValueError('Invalid native delay vector')
        if row['values']!=vectors[active]:raise ValueError('Active selector does not match dispatch')
    errors=[];results=[]
    for path in data['paths']:
        chain=path['path_sink_to_source']
        if not chain or chain[0]!=path['sink'] or chain[-1]!=path['source'] or len({tuple(n) for n in chain})!=len(chain):
            raise ValueError('Invalid endpoint chain')
        sums=[[0.]*4 for _ in range(3)]
        for index,coord in enumerate(chain):
            row=nodes[tuple(coord)];expected=chain[index+1] if index+1<len(chain) else None
            if row['parent']!=expected:raise ValueError('Incomplete/mismatched native parent chain')
            for s in range(3):
                sums[s]=[f32(a+b) for a,b in zip(sums[s],row['values_by_selector'][s])]
        delta=max(abs(max(sums[active])-path['maximum']),abs(min(sums[active])-path['minimum']))
        if delta>1e-6:errors.append(dict(sink=path['sink'],error=delta))
        native_name=path.get('report_name')
        if not native_name or not native_name.startswith(path['instance']+'.'):
            raise ValueError('Native report name does not identify the endpoint instance')
        endpoint=path['instance']+'/'+native_name[len(path['instance'])+1:]
        results.append(dict(endpoint=endpoint,sums_by_selector=sums))
    return dict(status='native_node_queries_replayed' if results and not errors else 'failed',nodes=len(nodes),paths=len(results),
        query_mismatches=errors,zero_contribution_nodes=sum(not any(r['values']) for r in nodes.values()),
        path_sums=results,full_timing_model_verified=False)


def compare_report(replayed,text):
    lookup={}
    for row in replayed['path_sums']:
        name=row['endpoint']
        if not name:raise ValueError('Native report endpoint name absent')
        if name in lookup and lookup[name]!=row['sums_by_selector']:raise ValueError('Ambiguous physical paths for report endpoint')
        lookup[name]=row['sums_by_selector']
    analysis=section=None;groups=defaultdict(list);missing=[]
    for lineno,line in enumerate(text.splitlines(),1):
        heading=re.match(r'^3\.(?:3|7)\.\d+ (Setup|Hold|Recovery|Removal) Analysis Report',line)
        if heading:analysis=heading[1];section=None
        if line.startswith('Data Arrival Path:'):section='arrival'
        if line.startswith('Data Required Path:'):section='required'
        match=re.match(r'^\s*[-\d.]+\s+([-\d.]+)\s+tNET\s+(RR|RF|FR|FF)\s+\d+\s+\S+\s+(\S+)\s*$',line)
        if not match:continue
        value,transition,name=match.groups()
        if name not in lookup:missing.append(dict(line=lineno,endpoint=name));continue
        groups[analysis,section,transition].append(dict(line=lineno,endpoint=name,reported_ns=float(value),sums=lookup[name]))
    summary=[]
    for key,rows in groups.items():
        candidates=[]
        for s in range(3):
            for slot in range(4):
                count=sum(abs(r['reported_ns']-r['sums'][s][slot])<=0.00051 for r in rows)
                candidates.append(dict(selector=s,slot=slot,matching_rows=count))
        best=max(candidates,key=lambda c:c['matching_rows']);s,slot=best['selector'],best['slot']
        residuals=[dict(line=r['line'],endpoint=r['endpoint'],reported_ns=r['reported_ns'],
            chain_ns=r['sums'][s][slot],residual_ns=r['reported_ns']-r['sums'][s][slot])
            for r in rows if abs(r['reported_ns']-r['sums'][s][slot])>0.00051]
        summary.append(dict(analysis=key[0],section=key[1],transition=key[2],rows=len(rows),
            best_observed_match=best,candidates=candidates,unexplained_residuals=residuals))
    if not summary:raise ValueError('No report rows compared')
    return dict(groups=summary,unmapped_rows=missing,
        rows=sum(g['rows'] for g in summary),matched_rows=sum(g['best_observed_match']['matching_rows'] for g in summary),
        scope='Best selector/slot correlation against rounded report rows, not proof of general corner or transition semantics. Residuals remain explicit.')


def with_local_delays(data,replayed,library):
    paths=[];adjusted=0
    for path,summed in zip(data['paths'],replayed['path_sums']):
        local=[list(library.passthrough(path['sink'][2],s,path['via_lutbuf'])) for s in range(3)]
        if local!=path['local_by_selector'] or local[data['active_selector']]!=path['local_adjustment']:
            raise ValueError('Local timing model does not match native endpoint adjustment')
        if path['via_lutbuf']:adjusted+=1
        paths.append(dict(endpoint=summed['endpoint'],sums_by_selector=[
            [f32(a+b) for a,b in zip(summed['sums_by_selector'][s],local[s])] for s in range(3)]))
    return dict(path_sums=paths,adjusted_paths=adjusted)


def audit(folder):
    folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());path=folder/'native_wire_delays.json'
    if (meta.get('status')!='captured' or meta.get('dll_sha256')!=EXPECTED or meta.get('own_pnr_result') is not False
            or meta.get('open_chip_database_used') is not False or not meta.get('cst_sha256')
            or hashlib.sha256(path.read_bytes()).hexdigest()!=meta.get('wire_delays_sha256')):
        raise ValueError('Unverified native wire-delay capture')
    data=json.loads(path.read_text());result=replay(data);report=folder/'probe.tr'
    result['report_comparison']=compare_report(result,report.read_text())
    bindings=folder/'primitive_bindings.json'
    if hashlib.sha256(bindings.read_bytes()).hexdigest()!=meta.get('bindings_sha256'):
        raise ValueError('Native primitive binding capture changed')
    instances={r['name']:r for r in json.loads(bindings.read_text())}
    for group in result['report_comparison']['groups']:
        for residual in group['unexplained_residuals']:
            instance=residual['endpoint'].rsplit('/',1)[0]
            residual['ff_companion_lut']=instances.get(instance,{}).get('ff_companion_lut')
    result['source_sha256']=meta['wire_delays_sha256'];result['report_sha256']=hashlib.sha256(report.read_bytes()).hexdigest()
    if 'cell_library_sha256' in meta and 'timing_file_sha256' in meta:
        from ownpr_cell_timing import NativeCellTiming
        corrected=with_local_delays(data,result,NativeCellTiming(folder))
        result['report_with_local_delays']=compare_report(corrected,report.read_text())
        result['local_adjusted_paths']=corrected['adjusted_paths']
    result['own_pnr_result']=False;result['timing_signoff']=False
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);args=ap.parse_args();r=audit(args.folder)
    (args.folder/'wire_delay_audit.json').write_text(json.dumps(r,indent=2))
    print(json.dumps({k:v for k,v in r.items() if k not in ('path_sums','report_comparison','report_with_local_delays')},indent=2))
    print('report rows',r['report_comparison']['rows'],'matched',r['report_comparison']['matched_rows'])
    if 'report_with_local_delays' in r:print('with local delays',r['report_with_local_delays']['matched_rows'])
    if r['status']=='failed':raise SystemExit(1)
