"""Whole-DAG LUT5/6/7 insertion feedback with fixed state/clock/hard boundaries.

Existing wide macros remain immutable. New isolated type-17 macros may replace
an unpaired LUT or feed a retained paired-root buffer. Every new dedicated
edge is checked geometrically; final native capture must validate it again.
"""
import copy
from ownpr_carry_feedback import FixedBoundaryCarryFeedback
from ownpr_lut_graph_feedback import LUTS
from ownpr_mux_architecture import MuxArchitecture
from ownpr_mux_macros import validate as validate_mux
from ownpr_carry_macros import validate as validate_carry
from ownpr_ff_configuration import compile_initial
from ownpr_timing_pins import pins
from ownpr_arrival_graph import senses

MUXS={'MUX2_LUT5','MUX2_LUT6','MUX2_LUT7'}
COMB=LUTS|MUXS


class WideMappingFeedback(FixedBoundaryCarryFeedback):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.mux_library=MuxArchitecture()

    def admit(self,design,layout,static):
        a,b=self.reference,design
        if {k:v for k,v in a.items() if k!='modules'}!={k:v for k,v in b.items() if k!='modules'} or set(a['modules'])!=set(b['modules']):raise ValueError('Changed design contract')
        if any(a['modules'][n]!=b['modules'][n] for n in a['modules'] if n!='top'):raise ValueError('Changed blackbox modules')
        x,y=a['modules']['top'],b['modules']['top'];old,new=x['cells'],y['cells']
        if {k:v for k,v in x.items() if k!='cells'}!={k:v for k,v in y.items() if k!='cells'}:raise ValueError('Changed port/net metadata')
        fixed=lambda cells:{n:c for n,c in cells.items() if c['type'] not in COMB}
        if fixed(old)!=fixed(new):raise ValueError('Changed state/IO/arithmetic/hard cells')
        mutable={'clusters','mux_clusters','wide_macros','mixed_carry_lut_calibration'}
        if {k:v for k,v in self.layout.items() if k not in mutable}!={k:v for k,v in layout.items() if k not in mutable}:raise ValueError('Changed fixed macro/state/IO geometry')
        for n,row in self.layout['clusters'].items():
            if row.get('ff') and layout['clusters'].get(n)!=row:raise ValueError('Moved paired register')
        if {r['ff'] for r in layout['clusters'].values() if r.get('ff')}!={r['ff'] for r in self.layout['clusters'].values() if r.get('ff')}:raise ValueError('Changed register placement membership')
        oldmacros={r['root']:r for r in self.layout.get('wide_macros',[])};newmacros={r['root']:r for r in layout.get('wide_macros',[])}
        if len(newmacros)!=len(layout.get('wide_macros',[])) or any(newmacros.get(n)!=r for n,r in oldmacros.items()):raise ValueError('Moved/changed existing wide macro')
        for n,row in self.layout.get('mux_clusters',{}).items():
            if new.get(n)!=old[n] or layout.get('mux_clusters',{}).get(n)!=row:raise ValueError('Changed existing dedicated mux')
        oldwide={(r['row'],r['column']) for r in oldmacros.values()}
        for n,row in self.layout['clusters'].items():
            if (row['row'],row['column']) in oldwide and (new.get(n)!=old[n] or layout['clusters'].get(n)!=row):raise ValueError('Changed existing wide leaf/sharing cell')
        for n in self.protected:
            if new.get(n)!=old[n] or layout['clusters'].get(n)!=self.layout['clusters'].get(n) or layout.get('mux_clusters',{}).get(n)!=self.layout.get('mux_clusters',{}).get(n):raise ValueError('Changed protected cell')
        sites=[]
        for n,row in layout['clusters'].items():
            site=row['row'],row['column'],row['slot'];sites.append(site)
            if any(type(v) is not int or v<0 for v in site) or site[2]>=8:raise ValueError('Invalid LUT site')
            prior=self.layout['clusters'].get(n)
            if site[0]>=self.device.rows or site[1]>=self.device.cols or self.device.grid[site[0]][site[1]]!=17:
                if row!=prior:raise ValueError('Changed uncalibrated LUT site')
        if len(sites)!=len(set(sites)):raise ValueError('LUT slot overlap')
        if set(layout['clusters'])!={n for n,c in new.items() if c['type'] in LUTS}:raise ValueError('LUT placement membership')
        for n,c in new.items():
            typ=c['type']
            if typ not in COMB:continue
            if typ in LUTS:
                width=int(typ[-1]);ports={**{f'I{i}':'input' for i in range(width)},'F':'output'};word=c.get('parameters',{}).get('INIT','')
                if set(c.get('parameters',{}))!={'INIT'} or len(word)!=(1<<width) or set(word)-{'0','1'}:raise ValueError('Invalid LUT truth contract')
            else:
                ports=dict(I0='input',I1='input',S0='input',O='output')
                if c.get('parameters',{}):raise ValueError('Uncalibrated mux parameters')
            if c['port_directions']!=ports or set(c['connections'])!=set(ports) or any(len(bs)!=1 for bs in c['connections'].values()):raise ValueError('Invalid combinational primitive ports')
            if n in old:
                if {k:v for k,v in old[n].items() if k not in ('type','parameters','connections','port_directions')}!={k:v for k,v in c.items() if k not in ('type','parameters','connections','port_directions')}:raise ValueError('Changed original combinational metadata')
                oldout='O' if old[n]['type'] in MUXS else 'F';newout='O' if typ in MUXS else 'F'
                if c['connections'][newout]!=old[n]['connections'][oldout]:raise ValueError('Renumbered original combinational output')
        for destination,source in self.paired_data.items():
            ff=destination.rsplit('/',1)[0];lut=source.rsplit('/',1)[0]
            if lut not in new or new[lut]['type'] not in LUTS or new[lut]['connections']['F']!=new[ff]['connections']['D']:raise ValueError('Changed paired direct data association')
        compile_initial(design,layout);validate_carry(design,layout,self.device);wide=validate_mux(design,layout,self.device)
        if layout.get('hard_clusters'):
            from ownpr_hard_architecture import HardArchitecture
            HardArchitecture().validate(design,layout,self.device)
        stable=lambda rows:[r for r in rows if r.get('kind') not in ('mux_dedicated_link','lut_truth_independent_constant_input')]
        if stable(self.static)!=stable(static):raise ValueError('Changed non-mux dedicated/static connections')
        expected={(r['source'],r['destination'],r['pin'],r['row'],r['column'],r['source_slot'],r['destination_slot']) for r in wide['links']}
        got={(r['source'],r['destination'],r['pin'],r['row'],r['column'],r['source_slot'],r['destination_slot']) for r in static if r.get('kind')=='mux_dedicated_link'}
        if got!=expected:raise ValueError('Actual dedicated mux links differ from placed hierarchy')
        return wide

    def rebuild(self,design,layout,logical,measurements,static):
        checked=self.admit(design,layout,static);cells=design['modules']['top']['cells'];constants={'0':0,'1':1};drivers={};inputs={};locations={}
        clocks={bit for c in cells.values() if c['type'].startswith('DFF') for bit in c['connections'].get('CLK',[])}
        for n,c in cells.items():
            for p,bit in pins(c,'output'):
                if bit in drivers:raise ValueError('Multiple timing drivers')
                drivers[bit]=n+'/'+p
                if c['type'] in ('GND','VCC'):constants[bit]=int(c['type']=='VCC')
            inputs.update({n+'/'+p:bit for p,bit in pins(c,'input')})
        wanted={e['destination'] for g in self.graphs.values() for e in g['edges'] if e['kind']=='wire' and self.reference['modules']['top']['cells'][e['destination'].rsplit('/',1)[0]]['type'] not in COMB}
        wanted.update(n+'/'+p for n,c in cells.items() if c['type'] in COMB for p,_ in pins(c,'input'))
        for row in logical:
            key=row['instance']+'/'+row['pin'];value=row['net'],row['node']
            if key not in wanted:continue
            if key in locations and locations[key]!=value:raise ValueError('Ambiguous physical timing input')
            locations[key]=value
        owners={}
        for net,values in measurements.items():
            for resource in values:
                if resource in owners and owners[resource]!=net:raise ValueError('Measured trees share receiver')
                owners[resource]=net
        direct={r['destination']+'/'+r['pin']:r['source']+'/'+r['source_pin'] for r in checked['links']}
        def wire(destination,mode,old=None):
            bit=inputs[destination];source=drivers.get(bit)
            if source is None:raise ValueError('Undriven timing input')
            if destination in self.paired_data:
                if old is None or source!=self.paired_data[destination] or old['source']!=source or old['delay']!=[0.,0.]:raise ValueError('Changed paired data arc')
                delay=[0.,0.]
            elif destination in direct:
                if source!=direct[destination]:raise ValueError('Incorrect dedicated mux source')
                delay=[0.,0.]
            elif bit in clocks:
                if old is None or source!=old['source']:raise ValueError('Changed fixed clock wire')
                delay=list(old['delay'])
            elif destination in locations:
                net,resource=locations[destination]
                if net not in measurements or resource not in measurements[net]:raise ValueError('Missing measured tree: '+destination)
                values=measurements[net][resource];delay=list(values[2:4] if mode=='hold' else values[:2])
            else:
                if old is None or source!=old['source']:raise ValueError('Changed unmeasured dedicated wire: '+destination)
                delay=list(old['delay'])
            return dict(source=source,destination=destination,delay=delay,transitions=[(0,0),(1,1)],kind='wire')
        result={}
        for mode,early in (('setup',False),('hold',True)):
            edges=[];nodes=set(self.graphs[mode]['seeds'])|set(self.model['hold_requirements' if early else 'setup_requirements'])
            for edge in self.graphs[mode]['edges']:
                a=edge['source'].rsplit('/',1)[0];b=edge['destination'].rsplit('/',1)[0];old=self.reference['modules']['top']['cells']
                if edge['kind']=='wire':
                    if old[b]['type'] not in COMB:edges.append(wire(edge['destination'],mode,edge))
                elif old[a]['type'] not in COMB and old[b]['type'] not in COMB:edges.append(copy.deepcopy(edge))
            for n,c in cells.items():
                typ=c['type']
                if typ not in COMB:continue
                out='F' if typ in LUTS else 'O';nodes.add(n+'/'+out)
                if typ in LUTS:
                    width=int(typ[-1]);bits=[c['connections']['I'+str(i)][0] for i in range(width)];functional=senses(int(c['parameters']['INIT'],2),width,{i:constants[b] for i,b in enumerate(bits) if b in constants})
                    records=[('I'+str(i),b,i) for i,b in enumerate(bits)]
                else:records=[(p,c['connections'][p][0],None) for p in ('I0','I1','S0')]
                for pin,bit,index in records:
                    if bit in constants:continue
                    destination=n+'/'+pin;edges.append(wire(destination,mode));transitions=[(0,0),(0,1),(1,0),(1,1)]
                    if typ in LUTS:
                        values=self.library.lut_input(index,layout['clusters'][n]['slot'],2 if early else 1);f= functional.get(index,[])
                    else:
                        row=layout['mux_clusters'][n];values=self.mux_library.delay(self.device.grid[row['row']][row['column']],row['level'],row['slot'],pin,2 if early else 1)
                        if pin in ('I0','I1'):transitions=[(0,0),(1,1)]
                        f=transitions
                    edges.append(dict(source=destination,destination=n+'/'+out,delay=list(values[:2] if early else values[2:4]),transitions=transitions,kind='cell',functional_transitions=f))
            for edge in edges:nodes.update((edge['source'],edge['destination']))
            result[mode]=dict(nodes=sorted(nodes),edges=edges,seeds=copy.deepcopy(self.graphs[mode]['seeds']))
        return result
