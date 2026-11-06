"""Independent hard-resource contracts and exclusive site legalization.

These are capacity/endpoint contracts. Clock routing, configuration integration
and whole-design timing remain separate gates; unsupported pin uses fail shut.
"""
import copy,json
from collections import Counter,defaultdict
from ownpr_device import ROOT,DLL_SHA
from ownpr_image_storage import sha
from ownpr_hard_timing_model import HardCellTiming,profile,NETLIST

HARD_TYPES={'DPX9B','SDPX9B','MULTALU27X18'}
PIN_CAPTURES=('native_bram18_full_timing_001','native_bram9_full_timing_001','native_bram36_full_timing_001','native_bram36_fullports_001','native_dsp_full_timing_001')


def read(path):return json.loads(path.read_text())


def constants(module):
    result={'0':0,'1':1}
    for cell in module['cells'].values():
        if cell['type'] in ('VCC','GND'):result[cell['connections']['V' if cell['type']=='VCC' else 'G'][0]]=int(cell['type']=='VCC')
    return result


def bits(cell):
    for port,values in cell['connections'].items():
        for index,bit in enumerate(values):yield port if len(values)==1 else f'{port}[{index}]',port,index,bit


def unused_pad_projection(before,after,pads,top='top'):
    """Prove new input buffers unobservable before projecting them out for CEC."""
    result=copy.deepcopy(after);original=before['modules'][top];module=result['modules'][top];seen=set()
    if module['ports']!=original['ports']:raise ValueError('Unused pad materialization changed ports')
    for row in pads:
        name=row['cell'];bit=row['bit'];port=row['port']
        if name in seen or name in original['cells'] or name not in module['cells']:raise ValueError('Invalid unused pad identity')
        seen.add(name);cell=module['cells'][name]
        if original['ports'][port]['direction']!='input' or bit not in original['ports'][port]['bits']:raise ValueError('Unused pad does not correspond to an input')
        if any(bit in bs for c in original['cells'].values() for bs in c['connections'].values()):raise ValueError('Input was not unused originally')
        if cell['type']!='IBUF' or cell['parameters'] or cell['port_directions']!={'I':'input','O':'output'} or cell['connections']['I']!=[bit] or len(cell['connections']['O'])!=1:raise ValueError('Unexpected added pad behavior')
        output=cell['connections']['O'][0]
        if any(output in bs for n,c in module['cells'].items() if n!=name for bs in c['connections'].values()) or any(output in p['bits'] for p in module['ports'].values()):
            raise ValueError('Added input buffer has an observable consumer')
        del module['cells'][name]
    return result


def assignment(costs):
    """Rectangular minimum-cost assignment, one exclusive site per cell."""
    n=len(costs)
    if not n:return []
    m=len(costs[0])
    if n>m or any(len(row)!=m for row in costs):raise ValueError('Insufficient/inconsistent hard site capacity')
    u=[0.]*(n+1);v=[0.]*(m+1);p=[0]*(m+1);way=[0]*(m+1)
    for i in range(1,n+1):
        p[0]=i;j0=0;minimum=[float('inf')]*(m+1);used=[False]*(m+1)
        while True:
            used[j0]=True;i0=p[j0];delta=float('inf');j1=0
            for j in range(1,m+1):
                if used[j]:continue
                cur=costs[i0-1][j-1]-u[i0]-v[j]
                if cur<minimum[j]:minimum[j]=cur;way[j]=j0
                if minimum[j]<delta:delta=minimum[j];j1=j
            if j1==0:raise ValueError('No finite hard resource assignment')
            for j in range(m+1):
                if used[j]:u[p[j]]+=delta;v[j]-=delta
                else:minimum[j]-=delta
            j0=j1
            if not p[j0]:break
        while j0:
            j1=way[j0];p[j0]=p[j1];j0=j1
    result=[None]*n
    for j in range(1,m+1):
        if p[j]:result[p[j]-1]=j-1
    if any(x is None for x in result) or len(set(result))!=n:raise ValueError('Invalid hard assignment result')
    return result


class HardArchitecture:
    def __init__(self):
        self.timing=HardCellTiming();self.templates={};self.sites=defaultdict(list);self.mode_attributes={}
        source=read(NETLIST)['modules']['VexRiscv'];ties=constants(source)
        for name in PIN_CAPTURES:
            folder=ROOT/'results'/name;meta=read(folder/'manifest.json');trials=read(folder/'binding_trials.json')
            if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['vendor_pnr_executed'] or sha(folder/'binding_trials.json')!=meta['trials_sha256']:
                raise ValueError('Independent hard port calibration required')
            for index,trial in enumerate(trials['records']):
                file=folder/f'site_{index:03d}'/'hard_resource_bindings.json'
                if sha(file)!=trial['capture_sha256']:raise ValueError('Hard port observation changed')
                row=read(file)['instances'][0];cell=source['cells'][row['instance']];key=profile(cell)
                attrs=row['physical_attributes']
                if cell['type']=='MULTALU27X18':
                    # Historical raw captures included pointer-vector bytes.
                    # The pinned constructor establishes exactly 248 fields.
                    if len(attrs) not in (248,256):raise ValueError('Unexpected DSP attribute snapshot shape')
                    attrs=attrs[:248]
                elif len(attrs)!=93:raise ValueError('Unexpected BRAM attribute snapshot shape')
                if key in self.mode_attributes and self.mode_attributes[key]!=attrs:raise ValueError('Hard mode attributes differ across calibrated samples')
                self.mode_attributes[key]=attrs
                _,r,c,_=row['site'];pins={p['pin']:{'direction':p['direction'],'nodes':[[a-r,b-c,k] for a,b,k in p['nodes']]} for p in row['pins']}
                logical={label:(port,index,bit) for label,port,index,bit in bits(cell)}
                if set(pins)!=set(logical):raise ValueError('Native/logical hard ports differ')
                template=self.templates.setdefault(key,dict(primitive=cell['type'],shape={p:len(bs) for p,bs in cell['connections'].items()},pins={},inactive_values={}))
                for label,pin in pins.items():
                    old=template['pins'].get(label)
                    if old and old['nodes'] and pin['nodes'] and old!=pin:raise ValueError('Hard port template changes between sites/instances')
                    if old is None or pin['nodes']:template['pins'][label]=pin
                    if not pin['nodes'] and pin['direction']=='In':
                        _,_,bit=logical[label];value=ties.get(bit)
                        if value is not None:template['inactive_values'].setdefault(label,set()).add(value)
        folder=ROOT/'results/native_hard_site_inventory_001';meta=read(folder/'manifest.json');file=folder/'hard_sites.json'
        if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['vendor_pnr_executed'] or sha(file)!=meta['sites_sha256']:raise ValueError('Unverified hard site inventory')
        for site in read(file)['sites']:
            family=site['family']
            header=site.get('site_header')
            if family=='new_dsp':header=next(x['site_header'] for x in site['observed_typed_members'] if x['object_class']=='.?AVmultalu27x18@NETLIST@@')
            if family not in ('bsram','new_dsp'):continue
            _,r,c,slot=header;self.sites[family].append(dict(row=r-1,column=c-1,slot=slot,inventory_id=site['id']))
        for family,expected in [('bsram',340),('new_dsp',298)]:
            self.sites[family].sort(key=lambda x:(x['row'],x['column']))
            if len(self.sites[family])!=expected:raise ValueError('Hard capacity differs')

    def contract(self,design,top='top'):
        module=design['modules'][top];cells=module['cells'];ties=constants(module);loads=Counter()
        for cell in cells.values():
            for port,values in cell['connections'].items():
                if cell['port_directions'][port]=='input':loads.update(values)
        for port in module['ports'].values():
            if port['direction']=='output':loads.update(port['bits'])
        result={}
        for name,cell in cells.items():
            if cell['type'] not in HARD_TYPES:continue
            key=profile(cell);template=self.templates.get(key)
            if template is None or key not in self.timing.profiles:raise ValueError('Uncalibrated hard mode: '+cell['type'])
            if template['shape']!={p:len(v) for p,v in cell['connections'].items()}:raise ValueError('Hard port widths differ')
            endpoints={};inactive=[];clock_pins=[]
            for label,port,index,bit in bits(cell):
                pin=template['pins'][label]
                if pin['direction']!=('In' if cell['port_directions'][port]=='input' else 'Out'):raise ValueError('Hard pin direction differs')
                if pin['nodes']:
                    endpoints[label]=dict(port=port,index=index,direction=cell['port_directions'][port],nodes=pin['nodes'])
                    if port in ('CLKA','CLKB','CLK'):clock_pins.append(label)
                else:
                    if pin['direction']=='In':
                        bypass_clock=cell['type']=='MULTALU27X18' and port=='CLK' and all(v=='BYPASS' for k,v in cell['parameters'].items() if k.endswith('_CLK'))
                        if not bypass_clock and ties.get(bit) not in template['inactive_values'].get(label,set()):raise ValueError('Uncalibrated inactive hard input use: '+name+'/'+label)
                        reason='bypass_clock' if bypass_clock else 'calibrated_constant_input'
                    else:
                        if bit not in ties and loads[bit]:raise ValueError('Used hard output has no calibrated node: '+name+'/'+label)
                        reason='unobserved_unused_output'
                    inactive.append(dict(pin=label,port=port,index=index,reason=reason))
            result[name]=dict(primitive=cell['type'],family='new_dsp' if cell['type']=='MULTALU27X18' else 'bsram',endpoints=endpoints,inactive=inactive,clock_pins=clock_pins)
        return result

    def legalize(self,contracts,positions,weights):
        result={}
        for family in sorted(self.sites):
            names=sorted(n for n,row in contracts.items() if row['family']==family);sites=self.sites[family]
            costs=[[max(1.,weights.get(n,1.))*((s['row']-positions[n][0])**2+(s['column']+1-positions[n][1])**2) for s in sites] for n in names]
            for name,index in zip(names,assignment(costs)):
                result[name]=dict(**sites[index],primitive=contracts[name]['primitive'],family=family,exclusive=True)
        return result

    def validate(self,design,placed,device,top='top'):
        contracts=self.contract(design,top);locations=placed.get('hard_clusters',{})
        if set(contracts)!=set(locations):raise ValueError('Missing/extra hard placement')
        occupied=set();endpoints=0
        for name,loc in locations.items():
            spec=contracts[name];available={(s['row'],s['column'],s['slot'],s['inventory_id']) for s in self.sites[spec['family']]}
            if (loc['row'],loc['column'],loc['slot'],loc['inventory_id']) not in available or loc['family']!=spec['family'] or loc['primitive']!=spec['primitive'] or not loc['exclusive']:raise ValueError('Illegal hard site/family')
            footprint={(loc['row'],loc['column']+j) for j in range(3)}
            if footprint&occupied:raise ValueError('Hard resource footprint collision')
            if any(device.grid[r][c]==17 for r,c in footprint):raise ValueError('Hard footprint overlaps ordinary CFU')
            occupied.update(footprint);endpoints+=len(spec['endpoints'])
        if any((g['row'],g['column']) in occupied for g in placed['clusters'].values()):raise ValueError('Hard/ordinary logic overlap')
        return dict(status='hard_capacity_and_port_contract_checked',hard_cells=len(contracts),endpoints=endpoints,
            families=dict(Counter(x['family'] for x in contracts.values())),exclusive_tiles=len(occupied),
            selected_sites_native_rebound=False,routing_verified=False,configuration_verified=False,timing_signoff=False)

    def selected_bindings(self,design,placed,folder,top='top'):
        meta=read(folder/'manifest.json');request=meta['request'];file=folder/'hard_resource_bindings.json'
        from pathlib import Path
        if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['vendor_pnr_executed'] or sha(file)!=meta['bindings_sha256']:
            raise ValueError('Selected hard bindings require independent native evidence')
        if sha(Path(request['placement']))!=request['placement_sha256'] or sha(Path(request['packed']))!=request['packed_sha256']:
            raise ValueError('Selected hard placement changed')
        if read(Path(request['placement']))!=placed or read(Path(request['packed']))!=design:raise ValueError('Hard capture does not match supplied implementation')
        contracts=self.contract(design,top);cells=design['modules'][top]['cells'];ties=constants(design['modules'][top]);allowed=defaultdict(set);local={}
        for n,spec in contracts.items():
            loc=placed['hard_clusters'][n]
            for label,e in spec['endpoints'].items():
                points={(loc['row']+1+a,loc['column']+1+b,i) for a,b,i in e['nodes']};local[n,label]=points
                allowed[cells[n]['connections'][e['port']][e['index']]].update(points)
        records=read(file)['instances'];result={};omitted=aliases=0
        if len(records)!=len(contracts) or {r['instance'] for r in records}!=set(contracts):raise ValueError('Incomplete selected hard capture')
        for row in records:
            n=row['instance'];cell=cells[n];loc=placed['hard_clusters'][n]
            if row['site'][1:3]!=[loc['row']+1,loc['column']+1]:raise ValueError('Selected hard site differs')
            logical={label:(port,index,bit) for label,port,index,bit in bits(cell)};pins={}
            if len(row['pins'])!=len(logical) or {p['pin'] for p in row['pins']}!=set(logical):raise ValueError('Incomplete selected hard ports')
            inactive={p['pin']:p for p in contracts[n]['inactive']}
            for pin in row['pins']:
                label=pin['pin'];port,index,bit=logical[label];observed=set(map(tuple,pin['nodes']));expected=local.get((n,label),set());reason=None
                if not observed:
                    if label in inactive:reason=inactive[label]['reason']
                    elif pin['direction']=='In' and bit in ties:reason='native_constant_pin_omitted';omitted+=1
                    else:raise ValueError('Used hard pin vanished at selected site: '+n+'/'+label)
                elif observed!=expected:
                    if label not in ('CEA','CEB') or not expected<=observed<=allowed[bit]:raise ValueError('Unverified selected hard endpoint alias: '+n+'/'+label)
                    aliases+=1
                pins[label]=dict(port=port,index=index,direction=cell['port_directions'][port],nodes=sorted(observed),inactive_reason=reason)
            result[n]=pins
        return dict(status='selected_hard_endpoints_verified',cells=result,placement_sha256=request['placement_sha256'],netlist_sha256=request['packed_sha256'],
            bindings_sha256=meta['bindings_sha256'],constant_pins_omitted=omitted,shared_ce_alias_pins=aliases,
            physical_routing_verified=False,configuration_verified=False,timing_signoff=False)
