"""Native-only architecture data for the independent P&R engine.

The discarded open-chip-database prototype is not used. This adapter reads
installed Gowin FSE and package JSON plus hash-bound DLL runtime snapshots.
Only verified coverage may be exposed to later physical algorithms.
"""
from collections import defaultdict,Counter
from pathlib import Path
import json,hashlib
import re
from functools import lru_cache
from extract_native_fse import Fse,SDK
from capture_runtime_tables import EXPECTED as DLL_SHA

ROOT=Path(__file__).resolve().parents[1]

class Device:
    def __init__(self,node_capture=ROOT/'results/native_route_nodes_003'):
        folder=Path(node_capture);manifest=json.loads((folder/'manifest.json').read_text())
        if manifest.get('status')!='captured' or manifest.get('open_chip_database_used') is not False or manifest.get('dll_sha256')!=DLL_SHA:
            raise ValueError('A native DLL snapshot with explicit provenance is required')
        if hashlib.sha256((folder/'snapshot.log').read_bytes()).hexdigest()!=manifest['snapshot_sha256']:
            raise ValueError('Native node capture changed')
        self.fse=Fse();self.grid=self.fse.grid;self.rows=len(self.grid);self.cols=len(self.grid[0])
        self.names={int(i):n for i,n in manifest['nodes'].items()};self.ids={n:i for i,n in self.names.items()}
        self.pins=json.loads((SDK/'data/device/GW5AST-138C/FCPBGA676A.json').read_text())
        self._tables={}

    @lru_cache(maxsize=1)
    def register_bindings(self):
        """Native physical nodes; input mux/FF modes are separately required."""
        folder=ROOT/'results/native_route_runtime_019'
        manifest=json.loads((folder/'manifest.json').read_text())
        path=folder/'native_register_ports.json'
        if (manifest.get('status')!='extracted' or manifest.get('open_chip_database_used') is not False
                or manifest.get('dll_sha256')!=DLL_SHA or manifest.get('placement_executed') is not False
                or manifest.get('sources',{}).get('GW5AST-138C.fse')!=self.fse.sha256
                or hashlib.sha256(path.read_bytes()).hexdigest()!=manifest.get('register_ports_sha256')):
            raise ValueError('Unverified native register bindings')
        data=json.loads(path.read_text());result={}
        if data['cfu_count']!=17280 or data['register_count']!=138240:raise ValueError('Incomplete register coverage')
        for row in data['templates']:
            slot=row['slot'];ports=row['ports']
            if slot in result or slot not in range(8) or row['site_count']!=data['cfu_count']:
                raise ValueError('Nonuniform/uncovered register template')
            if set(ports)!={'D','Q','CLK','CE','RESET'} or any(p['name'] not in self.ids or p['offset']!=[0,0] for p in ports.values()):
                raise ValueError('Unsupported native register endpoint')
            result[slot]={pin:p['name'] for pin,p in ports.items()}
        if set(result)!=set(range(8)):raise ValueError('Missing native register slots')
        return result

    @lru_cache(maxsize=4)
    def register_bindings_for(self,cell_type):
        """Class-specific control names, checked against actual native pins."""
        from ownpr_ff_configuration import FF_CONTROLS
        if cell_type not in FF_CONTROLS:raise ValueError('Uncalibrated FF type')
        base=self.register_bindings()
        if cell_type=='DFFRE':return base
        folder=ROOT/'results/native_ff_ports_001';meta=json.loads((folder/'manifest.json').read_text());path=folder/'ff_attributes.json'
        if (meta.get('status')!='captured' or meta.get('dll_sha256')!=DLL_SHA or meta.get('vendor_pnr_executed',False)
                or meta.get('vendor_placement_executed') is not False or meta.get('vendor_routing_executed') is not False
                or hashlib.sha256(path.read_bytes()).hexdigest()!=meta.get('attributes_sha256')):raise ValueError('Unverified extended FF pin capture')
        records=json.loads(path.read_text())['records'];observed=set()
        for record in records:
            if record['cell_type']!=cell_type:continue
            slot=record['slot'];observed.add(slot)
            for pin,node in record['native_pin_nodes'].items():
                expected=base[slot]['RESET' if pin=='SR' else pin]
                if node[:2]!=[50,90] or self.names[node[2]]!=expected:raise ValueError('FF physical control mapping differs')
        if observed!=set(range(8)):raise ValueError('Incomplete extended FF slots')
        return {slot:{(FF_CONTROLS[cell_type] if pin=='RESET' else pin):value for pin,value in ports.items()} for slot,ports in base.items()}

    def connections(self,tile_type):
        """Native table 2 tuples retain source byte offsets and config indices."""
        if tile_type not in self._tables:
            tables=[t for t in self.fse.blocks[str(tile_type)]['tables'] if t['type']==2]
            rows=[]
            for t in tables:
                for index,row in enumerate(self.fse.read_rows(t)):
                    src,dst=row[:2]
                    rows.append(dict(source=src,destination=dst,source_name=self.names.get(src),destination_name=self.names.get(dst),
                        file_offset=t['data_offset']+index*t['columns']*2,configuration_indices=row[2:]))
            self._tables[tile_type]=rows
        return self._tables[tile_type]

    @lru_cache(maxsize=1)
    def implicit_connections(self):
        folder=ROOT/'results/native_pip_encoding_011';manifest=json.loads((folder/'manifest.json').read_text())
        path=folder/'native_pip_writes.json'
        if (manifest.get('status')!='captured' or manifest.get('dll_sha256')!=DLL_SHA or manifest.get('open_chip_database_used') is not False
                or manifest.get('own_pnr_result') is not False or manifest.get('vendor_placement_executed') is not False
                or manifest.get('vendor_routing_executed') is not False
                or hashlib.sha256(path.read_bytes()).hexdigest()!=manifest.get('pip_writes_sha256')):
            raise ValueError('Unverified implicit native connection capture')
        capture=json.loads(path.read_text())
        if capture['source_fse_sha256']!=self.fse.sha256:raise ValueError('Implicit connection FSE version mismatch')
        result=defaultdict(list);destinations=defaultdict(set);cfu_types={17,221,222,223,224,225,226,227,228}
        fixed_pairs={('SN10','S110'),('SN10','N110'),('SN20','S120'),('SN20','N120'),
                     ('EW10','E110'),('EW10','W110'),('EW20','E120'),('EW20','W120')}
        for row in capture['records']:
            if not row.get('implicit'):continue
            typ,src,dst=row['tile_type'],row['source'],row['destination']
            if row.get('physical_topology_verified') is not True or row['set_bits'] or row['clear_bits']:
                raise ValueError('Implicit connection lacks physical topology/zero-write proof')
            r,c=row['sample_native_site']
            if self.grid[r-1][c-1]!=typ or src not in self.names or dst not in self.names:raise ValueError('Native connection sample mismatch')
            literal=self.connections(typ)
            if any(q['source']==src and q['destination']==dst for q in literal) or dst in destinations[typ]:
                raise ValueError('Duplicate or ambiguous zero-select input')
            destinations[typ].add(dst)
            has_mux=any(q['destination']==dst for q in literal)
            if typ not in cfu_types and (has_mux or (self.names[src],self.names[dst]) not in fixed_pairs):
                raise ValueError('Unverified non-CFU implicit mode')
            result[typ].append(dict(source=src,destination=dst,source_name=self.names[src],destination_name=self.names[dst],
                configuration_indices=(),file_offset=None,configuration_kind='implicit_zero' if has_mux else 'fixed_no_mux',
                native_capture_sha256=manifest['pip_writes_sha256']))
        grid_types={t for row in self.grid for t in row}
        if set(result)!=grid_types or any(len(v)!=(125 if t in cfu_types else 8) for t,v in result.items()):
            raise ValueError('Incomplete calibrated CFU implicit connection coverage')
        return result

    @lru_cache(maxsize=None)
    def routing_connections(self,tile_type):
        """Literal FSE rows plus calibrated CFU zero-select and fixed arcs."""
        extra=self.implicit_connections().get(tile_type,[])
        return self.connections(tile_type)+extra+self.hard_output_connections().get(tile_type,[])

    @lru_cache(maxsize=1)
    def hard_output_connections(self):
        folder=ROOT/'results/native_hard_default_pips_004';file=folder/'native_pip_writes.json'
        meta=json.loads((folder/'manifest.json').read_text());data=json.loads(file.read_text())
        if (meta.get('status')!='captured' or meta.get('dll_sha256')!=DLL_SHA or meta.get('open_chip_database_used') is not False
                or meta.get('vendor_placement_executed') is not False or meta.get('vendor_routing_executed') is not False
                or hashlib.sha256(file.read_bytes()).hexdigest()!=meta.get('pip_writes_sha256') or data['source_fse_sha256']!=self.fse.sha256):
            raise ValueError('Unverified hard-output default capture')
        result=defaultdict(list);seen=set();locations=defaultdict(list)
        for r,row in enumerate(self.grid):
            for c,typ in enumerate(row):locations[typ].append([r+1,c+1])
        for row in data['records']:
            sites=row.get('hard_default_sample_sites')
            if not sites:continue
            typ,src,dst=row['tile_type'],row['source'],row['destination'];key=typ,src,dst
            if (key in seen or self.names.get(src)!='Q0' or dst not in self.names or row['set_bits'] or row['clear_bits']
                    or row.get('implicit') is not True or row.get('physical_topology_verified') is not True
                    or len(sites) not in (1,2) or len(set(map(tuple,sites)))!=len(sites) or any(self.grid[r-1][c-1]!=typ for r,c in sites)
                    or len(sites)==1 and sites!=locations[typ]):
                raise ValueError('Hard-output default lacks topology and zero-write proof (single samples require a unique physical tile)')
            seen.add(key);literal=self.connections(typ)
            if any(q['source']==src and q['destination']==dst for q in literal) or not any(q['destination']==dst for q in literal):
                raise ValueError('Hard-output default does not belong to an existing FSE mux')
            if any(q['destination']==dst for q in self.implicit_connections().get(typ,[])):raise ValueError('Competing implicit mux choice')
            result[typ].append(dict(source=src,destination=dst,source_name=self.names[src],destination_name=self.names[dst],
                configuration_indices=(),file_offset=None,configuration_kind='implicit_zero',native_capture_sha256=meta['pip_writes_sha256']))
        if len(seen)!=5148 or len(result)!=234:raise ValueError('Incomplete calibrated non-CFU default coverage')
        return result

    def pin_site_name(self,pin):
        rows=[p for p in self.pins['PIN_DATA'] if p['INDEX']==pin]
        if len(rows)!=1 or rows[0]['TYPE']!='I/O':raise ValueError('Pin is not a unique native I/O site')
        return rows[0]['NAME']

    def inventory(self):
        return dict(device='GW5AST-138C',rows=self.rows,cols=self.cols,source_sha256=self.fse.sha256,
            source=str(self.fse.path),open_chip_database_used=False,tile_types=dict(Counter(t for row in self.grid for t in row)),
            named_normal_nodes=len(self.names),clock_network_verified=False,intertile_aliases_verified=False,
            full_device_placement_supported=False,timing_signoff=False)

class SwitchboxGraph:
    """A real native switchbox subgraph, not a claimed full-chip routing graph.

    Unknown node IDs remain excluded, with an explicit counter. Delay is an
    uncalibrated search cost: no operating frequency can be certified here.
    """
    def __init__(self,device,row,column):
        self.device=device;self.row=row;self.column=column;typ=device.grid[row][column]
        connections=device.routing_connections(typ);self.pips={};self.edges=defaultdict(list);self.coordinates=[(row,column)]*len(device.names)
        if set(device.names)!=set(range(len(device.names))):raise ValueError('Noncontiguous native node indices')
        self.excluded_connections=0
        for r in connections:
            if r['source_name'] is None or r['destination_name'] is None:self.excluded_connections+=1;continue
            src,dst=r['source'],r['destination'];pip=(row,column,r['source_name'],r['destination_name'])
            if pip not in self.pips:
                self.pips[pip]=r;self.edges[src].append((dst,1.0,pip))
        self.tile_type=typ
    def downhill(self,node):return tuple(self.edges[node])
    def node(self,row,column,name):
        if (row,column)!=(self.row,self.column):raise ValueError('Intertile routing has not been validated')
        return self.device.ids[name]
    def check_pip(self,pip):return tuple(pip) in self.pips

class RoutingGraph:
    """Full spatial graph of validated normal wires; special clocks excluded."""
    def __init__(self,device,rrg):
        import numpy as np
        self.np=np;self.device=device;folder=Path(rrg);m=json.loads((folder/'manifest.json').read_text())
        if m['status']!='built' or m['open_chip_database_used'] or m['source_fse_sha256']!=device.fse.sha256:raise ValueError('Native RRG provenance mismatch')
        for name,digest in m['arrays_sha256'].items():
            if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=digest:raise ValueError('RRG array changed')
        self.parent=np.load(folder/'parent.npy',mmap_mode='r');self.members=np.load(folder/'members.npy',mmap_mode='r');self.roots=np.load(folder/'roots.npy',mmap_mode='r')
        self.n=m['node_types'];self.cols=m['columns'];self.coordinates=self;self.heuristic_scale=0.05
        if self.n!=len(device.names):raise ValueError('Node-name version mismatch')
        self.allowed={i for i,n in device.names.items() if re.fullmatch(r'(?:[ABCD]\d+|[FQ]\d+|OF\d+|X0\d+|[NESW][128]\d[0-8]|(?:SN|EW)[12]0|SEL\d+|CLK\d+|CE\d+|LSR\d+|VCC|VSS|CIN\d+|COUT\d+)',n)}
        self._templates={};self._reverse_templates={}
        self.selected_terminal_pips=set();self._terminal_out=defaultdict(list);self._terminal_in=defaultdict(list)
    def select_terminal_pips(self,pips):
        selected={tuple(p) for p in pips}
        if self.selected_terminal_pips and selected!=self.selected_terminal_pips:raise ValueError('Terminal selections changed on an active graph')
        if selected==self.selected_terminal_pips:return
        for r,c,src,dst in selected:
            if (src,dst) not in {('OF0','DI0'),('OF1','DI1'),('OF3','DI3')} or self.device.grid[r][c] not in (17,228):raise ValueError('Uncalibrated selected FF terminal')
            if not any(q['source_name']==src and q['destination_name']==dst for q in self.device.connections(self.device.grid[r][c])):raise ValueError('FF terminal is absent from native FSE')
            a,b=self.node(r,c,src),self.node(r,c,dst);pip=(r,c,src,dst)
            self._terminal_out[a].append((b,1.0,pip));self._terminal_in[b].append((a,1.0,pip))
        self.selected_terminal_pips=selected
        RoutingGraph.downhill.cache_clear();RoutingGraph.uphill.cache_clear()
    def __getitem__(self,node):return (int(node)//self.n//self.cols,int(node)//self.n%self.cols)
    def node(self,r,c,name):return int(self.parent[(r*self.cols+c)*self.n+self.device.ids[name]])
    def template(self,typ):
        if typ not in self._templates:
            edges=defaultdict(list)
            for row in self.device.routing_connections(typ):
                a,b=row['source'],row['destination']
                if a in self.allowed and b in self.allowed:edges[a].append(b)
            self._templates[typ]={a:sorted(set(bs)) for a,bs in edges.items()}
            rev=defaultdict(list)
            for a,bs in self._templates[typ].items():
                for b in bs:rev[b].append(a)
            self._reverse_templates[typ]=dict(rev)
        return self._templates[typ]
    @lru_cache(maxsize=100000)
    def downhill(self,node):
        # Match the array dtype: a Python int can force a full int64 conversion
        # of this multi-million-entry mmap in older NumPy versions per lookup.
        key=self.np.uint32(node)
        a=int(self.np.searchsorted(self.roots,key,'left'));b=int(self.np.searchsorted(self.roots,key,'right'));result=[]
        for raw in self.members[a:b]:
            raw=int(raw);index=raw%self.n;r,c=self[raw]
            for dest in self.template(self.device.grid[r][c]).get(index,()):
                target=int(self.parent[(r*self.cols+c)*self.n+dest])
                if target==node:continue
                # Uncalibrated unit cost; full timing model is a separate gate.
                result.append((target,1.0,(r,c,self.device.names[index],self.device.names[dest])))
        return tuple(result)+tuple(self._terminal_out.get(node,()))
    def check_pip(self,pip):
        r,c,src,dst=pip
        return tuple(pip) in self.selected_terminal_pips or self.device.ids[dst] in self.template(self.device.grid[r][c]).get(self.device.ids[src],())
    @lru_cache(maxsize=100000)
    def uphill(self,node):
        key=self.np.uint32(node);a=int(self.np.searchsorted(self.roots,key,'left'));b=int(self.np.searchsorted(self.roots,key,'right'));result=[]
        for raw in self.members[a:b]:
            raw=int(raw);index=raw%self.n;r,c=self[raw];typ=self.device.grid[r][c];self.template(typ)
            for source in self._reverse_templates[typ].get(index,()):
                before=int(self.parent[(r*self.cols+c)*self.n+source])
                if before!=node:result.append((before,1.0,(r,c,self.device.names[source],self.device.names[index])))
        return tuple(result)+tuple(self._terminal_in.get(node,()))
