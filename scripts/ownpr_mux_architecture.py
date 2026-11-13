"""Native-derived dedicated LUT5/6/7 mux geometry and delay model.

No cross-CFU LUT8 or shared FF/ALU packing is certified by this slice.
"""
from ownpr_image_storage import read_text as read_artifact_text
import hashlib,json,re
from pathlib import Path
from ownpr_device import ROOT,DLL_SHA,Device
from extract_timing import extract,ROW

INVENTORY=ROOT/'results/native_mux_inventory_002'
REFERENCE=ROOT/'results/native_uart_mux_calibration_005'


def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def children(level,slot):
    if level==5 and slot in (0,2,4,6):return [('LUT4',slot),('LUT4',slot+1)]
    if level==6 and slot in (1,5):return [('MUX2_LUT5',slot+1),('MUX2_LUT5',slot-1)]
    if level==7 and slot==3:return [('MUX2_LUT6',5),('MUX2_LUT6',1)]
    raise ValueError('Unsupported dedicated mux level/slot; LUT8 requires separate cross-CFU rules')


def geometry_audit(folder):
    folder=Path(folder);meta=read(folder/'manifest.json')
    if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or not meta['vendor_placement_executed'] or not meta['vendor_routing_executed'] or meta['own_pnr_result']:
        raise ValueError('Unverified dedicated mux reference')
    if sha(folder/'primitive_bindings.json')!=meta['bindings_sha256'] or sha(folder/'probe.vg')!=meta['source_sha256']:raise ValueError('Mux reference files changed')
    text=(folder/'probe.vg').read_text();cells={};drivers={}
    for typ,name,body in re.findall(r'^\s*(LUT[1-4]|MUX2_LUT[5-8])\s+([^\s(]+)\s*\((.*?)\);',text,re.M|re.S):
        pins={p:v.strip().lstrip('\\') for p,v in re.findall(r'\.(\w+)\(([^()]*)\)',body)};cells[name]=dict(type=typ,pins=pins)
        drivers[pins['O' if typ.startswith('MUX') else 'F']]=name
    native={r['name']:r for r in read(folder/'primitive_bindings.json')};expected={n for n,c in cells.items() if c['type'].startswith('MUX')};actual={n for n,c in native.items() if c['logical_class']=='.?AVMux2@NETLIST@@'}
    if expected!=actual or not actual:raise ValueError('Mux instance set changed or is empty')
    d=Device();links=[];seen=set();levels=set()
    for name in sorted(actual):
        row=native[name];typ,r,c,slot=row['physical_header'];level=int(cells[name]['type'][-1]);levels.add(level)
        if typ!=level+28 or row['mux_subtype']!=typ or d.grid[r-1][c-1]!=17 or (r,c,slot) in seen:raise ValueError('Unsupported/conflicting reference mux site')
        seen.add((r,c,slot))
        if row.get('physical_mux_cached_logic_matches') is not True:raise ValueError('Missing dedicated mux logical cache')
        for pin,(source_type,source_slot) in zip(('I0','I1'),children(level,slot)):
            source=drivers[cells[name]['pins'][pin]];got=next(p for p in row['pins'] if p['pin']==pin);source_header=[24 if source_type=='LUT4' else int(source_type[-1])+28,r,c,source_slot]
            if cells[source]['type']!=source_type or got['driver']!=source or got['driver_site']!=source_header or native[source]['physical_header']!=source_header:raise ValueError('Dedicated mux data input is reversed or misplaced')
            if got['node'] is not None:raise ValueError('Unexpected ordinary node on dedicated mux input')
            links.append(dict(source=source,destination=name,pin=pin,source_site=source_header,destination_site=row['physical_header']))
        for pin,node in (('S0',133+slot),('O',48+slot)):
            if next(p['node'] for p in row['pins'] if p['pin']==pin)!=[r,c,node]:raise ValueError('Mux selectable endpoint binding changed')
    return dict(status='native_mux_geometry_matched',muxes=len(actual),levels=sorted(levels),internal_links=links,
        source_sha256=dict(bindings=meta['bindings_sha256'],netlist=meta['source_sha256']),arbitrary_boundary_legality_verified=False,own_pnr_result=False)


def report_audit(folder,require_internal_rows=True,capture_context=None):
    folder=Path(folder);data=read(folder/'mux_timing_queries.json');muxes={r['instance']:r for r in data['muxes']};table={}
    for name,row in muxes.items():
        for q in row['queries']:table[name,q['pin'],q['selector']]=q['values']
    observed=extract(folder/'probe.tr',folder/'probe.vg');arcs=[]
    for r in observed['arcs']:
        if r['instance'] not in muxes or r['kind']!='tINS':continue
        if r['to_pin']!='O' or r['transition'] not in ('RR','RF','FR','FF'):raise ValueError('Unsupported mux report arc')
        maximum=r['analysis'] in ('setup','recovery');vector=table[r['instance'],r['from_pin'],1 if maximum else 2];predicted=vector[(2 if maximum else 0)+int(r['transition'][-1]=='F')]
        if abs(r['delay_ns']-predicted)>.00051:raise ValueError('Mux cell query does not match native report')
        arcs.append(dict(instance=r['instance'],pin=r['from_pin'],analysis=r['analysis'],observed=r['delay_ns'],predicted=predicted))
    previous=None;wire_rows=0
    for line in read_artifact_text(folder/'probe.tr').splitlines():
        if line.startswith(('Data Arrival Path:','Data Required Path:','Path Statistics:')):previous=None
        row=ROW.match(line)
        if not row:continue
        _,delay,typ,_,_,_,endpoint=row.groups();name,sep,pin=endpoint.rpartition('/')
        if typ=='tNET' and name in muxes and pin in ('I0','I1'):
            source=next(r for r in muxes[name]['links'] if r['pin']==pin)
            if not source['timing_edge_present'] or previous is None or previous[0]!=source['source'] or float(delay)!=0. or source['minimum_ns']!=0. or source['maximum_ns']!=0.:
                raise ValueError('Dedicated mux report edge is missing or nonzero')
            wire_rows+=1
        previous=(name,pin) if sep else None
    unreported=None
    if not arcs:
        if require_internal_rows or capture_context is None:raise ValueError('Empty mux timing report evidence')
        # A newly faster macro need not occur on any worst-per-endpoint path.
        # Revalidate every actual mux, all 9 delay vectors and both real STA
        # input edges against the independent calibrated model. Mere presence
        # of a query file or an asserted success flag cannot admit this case.
        unreported=MuxArchitecture().check_capture(data,*capture_context)
        if not unreported['muxes'] or unreported['query_vectors']!=9*unreported['muxes'] or unreported['direct_timing_edges']!=2*unreported['muxes']:
            raise ValueError('Incomplete unreported mux query coverage')
    if require_internal_rows and not wire_rows:raise ValueError('Empty mux timing report evidence')
    return dict(status='native_mux_report_matched' if arcs else 'native_mux_queries_matched_no_report_arcs',cell_arcs=len(arcs),internal_net_rows=wire_rows,checks=arcs,
        internal_edges_covered_by_report=wire_rows>0,cell_arcs_covered_by_report=bool(arcs),unreported_capture_verified=unreported)


class MuxArchitecture:
    def __init__(self):
        meta=read(INVENTORY/'manifest.json');file=INVENTORY/'mux_bindings.json'
        if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['vendor_pnr_executed'] or meta['open_chip_database_used'] or sha(file)!=meta['bindings_sha256']:
            raise ValueError('Unverified standalone mux inventory')
        self.bindings={};self.delays={};self.sha256=meta['bindings_sha256']
        for r in read(file):
            level=r['level'];slot=r['slot'];typ=r['tile_type']
            if level==8:continue
            children(level,slot);key=typ,level,slot
            if key in self.bindings:raise ValueError('Duplicate mux inventory entry')
            pins={p['pin']:p for p in r['pins']}
            if set(pins)!={'I0','I1','S0','O'} or any(pins[p]['node'] is not None for p in ('I0','I1')):raise ValueError('Unsupported mux inventory pins')
            if pins['S0']['node']!=r['site']+[133+slot] or pins['O']['node']!=r['site']+[48+slot]:raise ValueError('Mux physical endpoint mismatch')
            self.bindings[key]=dict(S0=pins['S0']['node_name'],O=pins['O']['node_name']);queries={}
            for q in r['queries']:
                qkey=q['pin'],q['selector']
                if qkey in queries:raise ValueError('Duplicate mux delay query')
                queries[qkey]=q['values']
            if set(queries)!={(p,s) for p in ('I0','I1','S0') for s in range(3)}:raise ValueError('Incomplete mux delay vectors')
            self.delays[key]=queries
        if len(self.bindings)!=63:raise ValueError('Incomplete nine-type mux slot coverage')
        self.geometry=geometry_audit(REFERENCE);reference=read(REFERENCE/'manifest.json');capture=REFERENCE/'mux_timing_queries.json'
        if sha(capture)!=reference['mux_timing_sha256']:raise ValueError('Mux timing reference changed')
        device=Device()
        for r in read(capture)['muxes']:
            _,row,column,slot=r['site'];typ=device.grid[row-1][column-1]
            for q in r['queries']:
                if self.delays[typ,r['level'],slot][q['pin'],q['selector']]!=q['values']:raise ValueError('Standalone and completed mux timing queries differ')
            if len(r['links'])!=2 or any(not x['timing_edge_present'] or x['minimum_ns']!=0 or x['maximum_ns']!=0 for x in r['links']):raise ValueError('Unverified internal mux timing edges')
        self.report=report_audit(REFERENCE)

    def pins(self,tile_type,level,slot):
        if (tile_type,level,slot) not in self.bindings:raise ValueError('Uncalibrated mux pin binding')
        return dict(self.bindings[tile_type,level,slot])

    def delay(self,tile_type,level,slot,pin,selector):
        return tuple(self.delays[tile_type,level,slot][pin,selector])

    def check_capture(self,data,design,placement,mapping):
        from ownpr_mux_macros import validate
        d=Device();check=validate(design,placement,d);aliases=mapping['instances'];expected={aliases[n]:r for n,r in placement['mux_clusters'].items()}
        links={(aliases[r['destination']],r['pin']):r for r in check['links']};seen=set();edges=0;vectors=0
        for row in data['muxes']:
            name=row['instance']
            if name in seen or name not in expected:raise ValueError('Unexpected captured mux')
            seen.add(name);loc=expected[name];r,c,s=loc['row'],loc['column'],loc['slot'];level=loc['level'];typ=d.grid[r][c]
            if row['level']!=level or row['site']!=[level+28,r+1,c+1,s]:raise ValueError('Captured mux placement changed')
            qseen=set()
            for q in row['queries']:
                key=q['pin'],q['selector']
                if key in qseen or q['values']!=self.delays[typ,level,s].get(key):raise ValueError('Own mux delay differs from calibrated dispatcher')
                qseen.add(key);vectors+=1
            if len(qseen)!=9 or len(row['links'])!=2 or {x['pin'] for x in row['links']}!={'I0','I1'}:raise ValueError('Incomplete own mux timing queries')
            for link in row['links']:
                spec=links[name,link['pin']];source=design['modules']['top']['cells'][spec['source']];st=24 if source['type']=='LUT4' else int(source['type'][-1])+28
                if link['source']!=aliases[spec['source']] or link['source_site']!=[st,r+1,c+1,spec['source_slot']]:raise ValueError('Own mux direct input is reversed/misplaced')
                if not link['timing_edge_present'] or link['minimum_ns']!=0 or link['maximum_ns']!=0:raise ValueError('Own mux internal edge differs from reference')
                edges+=1
        if seen!=set(expected):raise ValueError('Missing own mux timing records')
        return dict(muxes=len(seen),query_vectors=vectors,direct_timing_edges=edges,model_matched=True,arbitrary_boundary_legality_verified=False)
