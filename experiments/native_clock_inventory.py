"""Read initialized native clock topology without placement or routing."""
import argparse,json,os,re,shutil,struct,subprocess,sys,time
from collections import deque,Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from run_abc_reproduction import sha
from capture_runtime_tables import EXPECTED


def worker(out):
    from native_device_session import NativeDeviceSession
    import ctypes as C
    native=NativeDeviceSession(out);ptr=native.ptr;vp=C.c_void_p
    names={int(k):v for k,v in json.loads((ROOT/'results/native_route_nodes_003/manifest.json').read_text())['nodes'].items()}
    names_file=ROOT/'results/native_route_runtime_019/native_clock_names.json'
    if names_file.exists():clock_names=json.loads(names_file.read_text())
    else:
        # The retained, hash-checked topology contains the same native template
        # names, allowing replay after historical auxiliary-file cleanup.
        from ownpr_clock_graph import ClockGraph
        retained=ClockGraph(ROOT/'results/native_clock_inventory_005')
        vectors=retained.data['template_vectors'];labels=[e['name'] for e in vectors[0]['entries']]
        if len(labels)!=335 or any([e['name'] for e in v['entries']]!=labels[:len(v['entries'])] for v in vectors):raise ValueError('Retained clock template names are inconsistent')
        clock_names=dict(cmux_names=labels[:309],cmux_aux_names=labels[309:])
    cmux=ptr(native.chip+0x80)
    if not cmux:raise ValueError('Native physical CMUX object absent')
    def point(node):return tuple(struct.unpack('<3h',C.string_at(node,6)))
    def fanouts(node):
        count=C.c_short.from_address(node+6).value
        if not 0<=count<=16000:raise ValueError('Unexpected native clock degree')
        return [p for i in range(count) if (p:=native.getout(node,i))]
    table=native.fn('?getRouteNodeTable@globalMem@@QEAAPEAVrouteNodeTable@TEMPLATE@@XZ',vp,[vp])(native.mem)
    clock_templates={};template_vectors=[]
    for group,offset in enumerate((0x3260,0x3278,0x3290)):
        begin,end=ptr(table+offset),ptr(table+offset+8);count=(end-begin)//8
        if not begin or (end-begin)%8 or count not in (309,335):raise ValueError('Unexpected native CMUX template vector')
        entries=[]
        for index in range(count):
            node=ptr(begin+8*index)
            if not node:raise ValueError('Null CMUX template')
            name=clock_names['cmux_names'][index] if index<309 else clock_names['cmux_aux_names'][index-309]
            clock_templates[node]=dict(name=name,namespace=f'cmux_group_{group}')
            entries.append(dict(index=index,name=name,coordinate=list(point(node))))
        template_vectors.append(dict(group=group,entries=entries))
    span_labels={}
    def descriptor(node):
        r,c,i=point(node)
        if node in clock_templates:
            name=clock_templates[node]['name'];namespace=clock_templates[node]['namespace']
        elif node in span_labels:
            group,index=span_labels[node];name=f'span_group_{group}_entry_{index}';namespace='physical_span'
        elif r>=0 and c>=0 and i in names:name=names[i];namespace='grid'
        else:
            name=None;namespace='special_unclassified'
        return dict(coordinate=[r,c,i],name=name,namespace=namespace)
    seeds=[];arrays=[]
    # buildPclkSpanRRGraph_5AT138 reads [chip+0x80]->{0,8}, then
    # entries 1..32 as node pointers. No guessed object extent is traversed.
    for offset in (0,8):
        array=ptr(cmux+offset)
        if not array:raise ValueError('Missing native span array')
        records=[]
        for index in range(1,33):
            node=ptr(array+8*index)
            if node:
                span_labels[node]=(offset//8,index)
                records.append(dict(index=index,**descriptor(node)));seeds.append(node)
        arrays.append(dict(offset=offset,nodes=records))
    print('SPAN_ARRAYS',[(a['offset'],a['nodes'][:3]) for a in arrays],flush=True)
    ids={};records=[];edges=[];queue=deque();terminals=set()
    def add(node):
        if node not in ids:
            ids[node]=len(records);records.append(dict(id=ids[node],**descriptor(node)));queue.append(node)
        return ids[node]
    for node in seeds:add(node)
    source_request=json.loads((out/'source_request.json').read_text()) if (out/'source_request.json').exists() else dict(package_pin='L22',coordinate=[85,182,38])
    source=native.node(tuple(source_request['coordinate']));source_targets=fanouts(source);source_id=add(source)
    dedicated_entries=[]
    for target in source_targets:
        r,c,_=point(target)
        if r<0 or c<0:
            ident=add(target);edges.append([source_id,ident]);dedicated_entries.append(ident)
    while queue:
        node=queue.popleft();info=records[ids[node]];name=info['name'] or ''
        internal=info['namespace']!='grid' or name.startswith(('GB','GT','SPCLK'))
        if not internal:terminals.add(ids[node]);continue
        for target in fanouts(node):edges.append([ids[node],add(target)])
        if len(records)>600000:
            print('BOUNDED_INVENTORY',Counter(r['namespace'] for r in records),Counter(r['name'] for r in records).most_common(24),flush=True)
            raise ValueError('Clock inventory exceeded bounded scope')
    source_fanouts=[descriptor(p) for p in source_targets]
    (out/'clock_topology.json').write_text(json.dumps(dict(seeds=[ids[p] for p in seeds],nodes=records,edges=edges,
        terminal_ids=sorted(terminals),arrays=arrays,template_vectors=template_vectors,
        fixed_clock_source=dict(package_pin=source_request['package_pin'],id=source_id,dedicated_entry_ids=dedicated_entries,node=descriptor(source),fanouts=source_fanouts),
        ordinary_nodes_not_traversed=True,longwire_branches_not_traversed=True,clock_configuration_verified=False,timing_signoff=False,
        scope='Native primary clock spans and fixed clock pin dedicated entries, stopping at ordinary and long-wire grid endpoints. CMUX names require pointer membership in native vectors. Configuration remains to be audited.'),separators=(',',':')))
    print('CLOCK_TOPOLOGY',len(seeds),'SEEDS',len(records),'NODES',len(edges),'EDGES',len(terminals),'TERMINALS',flush=True)
    print('SPAN_ARRAYS',[(a['offset'],a['nodes'][:2]) for a in arrays],flush=True)
    print('FIXED_INPUT_FANOUTS',source_fanouts,flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--worker',action='store_true');ap.add_argument('--source-pin',default='L22');args=ap.parse_args();out=args.out.resolve()
    if args.worker:return worker(out)
    from workspace_budget import reserve
    from ownpr_device import Device
    reserve(ROOT,150_000_000);device=Device();site=device.pin_site_name(args.source_pin);side,index,letter=re.fullmatch(r'IO([TBLR])(\d+)([AB])',site).groups();index=int(index)
    runtime=ROOT/'results/native_route_runtime_007';meta=json.loads((runtime/'manifest.json').read_text());file=runtime/'native_iob_ports.json'
    if sha(file)!=meta['iob_ports_sha256']:raise ValueError('Native I/O port capture changed')
    r,c={'T':(0,index),'B':(meta['rows']-1,index),'L':(index,0),'R':(index,meta['columns']-1)}[side]
    found=[x for x in json.loads(file.read_text()) if (x['native_row'],x['native_column'],x['slot'])==(r,c,ord(letter)-65)]
    if len(found)!=1 or len(found[0]['buffers'])!=1 or not found[0]['buffers'][0]['out']:raise ValueError('No unique input-buffer clock source')
    prepare(out);(out/'source_request.json').write_text(json.dumps(dict(package_pin=args.source_pin,coordinate=found[0]['buffers'][0]['out'],native_iob_capture_sha256=sha(file)),indent=2))
    shutil.copyfile(__file__,out/'extractor_source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;expired=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=python.parent,
            env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=120).returncode
        except subprocess.TimeoutExpired:expired=True
    result=dict(status='captured' if rc==0 and (out/'clock_topology.json').exists() else 'failed',returncode=rc,timeout=expired,
        wall_seconds=time.monotonic()-start,dll_sha256=EXPECTED,open_chip_database_used=False,vendor_pnr_executed=False,
        device_programmed=False,own_pnr_result=False)
    if result['status']=='captured':result['topology_sha256']=sha(out/'clock_topology.json')
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()
