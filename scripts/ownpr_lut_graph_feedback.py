"""Rebuild LUT topology and measured wires under fixed state/clock boundaries.

Ordinary LUT functions, counts and unpaired positions may change. All other
cells and physical macro contracts remain identical. Admission is separate
from CEC, physical routing validation, and mandatory final native STA.
"""
import copy
from ownpr_bidir_timing import analyze
from ownpr_timing_pins import pins
from ownpr_arrival_graph import senses
from ownpr_state_safety import protected_cells

LUTS={'LUT1','LUT2','LUT3','LUT4'}


class FixedBoundaryLutFeedback:
    def __init__(self,model,graphs,reference,layout,static,library,device=None):
        self.model=model;self.graphs=graphs;self.reference=reference;self.layout=layout;self.static=static;self.library=library
        self.device=device
        self.protected=protected_cells(reference)
        # Normal paired LUT->FF links are implicit zero-wire connections.
        # Wide-root FFs instead use a real OF->DI PIP and must be measured.
        self.paired_data={r['ff']+'/D':r['lut']+'/F' for r in layout['clusters'].values() if r.get('ff') and not r.get('wide_ff_root') and not r.get('independent_ff_input')}

    def admit(self,design,layout,static):
        a,b=self.reference,design
        if {k:v for k,v in a.items() if k!='modules'}!={k:v for k,v in b.items() if k!='modules'} or set(a['modules'])!=set(b['modules']):raise ValueError('Changed design contract')
        if any(a['modules'][n]!=b['modules'][n] for n in a['modules'] if n!='top'):raise ValueError('Changed blackbox modules')
        x,y=a['modules']['top'],b['modules']['top']
        if {k:v for k,v in x.items() if k!='cells'}!={k:v for k,v in y.items() if k!='cells'}:raise ValueError('Changed port/net metadata contract')
        old,new=x['cells'],y['cells']
        fixed=lambda cells:{n:c for n,c in cells.items() if c['type'] not in LUTS}
        if fixed(old)!=fixed(new):raise ValueError('Changed fixed state, IO, arithmetic, mux or hard cell')
        if {k:v for k,v in self.layout.items() if k!='clusters'}!={k:v for k,v in layout.items() if k!='clusters'}:raise ValueError('Changed fixed macro/IO/clock placement')
        for n,row in self.layout['clusters'].items():
            if row.get('ff') and layout['clusters'].get(n)!=row:raise ValueError('Moved or changed a paired register')
        old_pairs={r['ff'] for r in self.layout['clusters'].values() if r.get('ff')}
        if {r['ff'] for r in layout['clusters'].values() if r.get('ff')}!=old_pairs:raise ValueError('Changed paired register set')
        sites=[]
        from ownpr_critical_chain_move import exclusive_tiles
        excluded=exclusive_tiles(self.layout)
        for n,row in layout['clusters'].items():
            site=(row['row'],row['column'],row['slot']);sites.append(site)
            if any(type(v) is not int or v<0 for v in site) or site[2]>=8:raise ValueError('Invalid LUT site')
            if self.device and (site[0]>=self.device.rows or site[1]>=self.device.cols or self.device.grid[site[0]][site[1]]!=17):
                oldrow=self.layout['clusters'].get(n)
                if row!=oldrow:raise ValueError('Uncalibrated changed LUT tile')
            oldrow=self.layout['clusters'].get(n)
            if (site[:2] in excluded or oldrow and (oldrow['row'],oldrow['column']) in excluded) and (row!=oldrow or new.get(n)!=old.get(n)):raise ValueError('Changed LUT in an exclusive macro tile')
        if len(sites)!=len(set(sites)):raise ValueError('Overlapping LUT slots')
        for destination,source in self.paired_data.items():
            ff=destination.rsplit('/',1)[0];lut=source.rsplit('/',1)[0]
            if lut not in new or new[lut]['connections']['F']!=new[ff]['connections']['D']:raise ValueError('Changed paired data association')
        for n in self.protected:
            if new.get(n)!=old[n] or layout['clusters'].get(n)!=self.layout['clusters'].get(n):raise ValueError('Changed protected cell')
        for n,c in new.items():
            if c['type'] not in LUTS:continue
            width=int(c['type'][-1]);ports={**{f'I{i}':'input' for i in range(width)},'F':'output'};word=c.get('parameters',{}).get('INIT','')
            if set(c.get('parameters',{}))!={'INIT'} or len(word)!=1<<width or set(word)-{'0','1'} or c['port_directions']!=ports or set(c['connections'])!=set(ports) or any(len(v)!=1 for v in c['connections'].values()):raise ValueError('Uncalibrated LUT primitive')
            if n not in layout['clusters'] or layout['clusters'][n]['lut']!=n:raise ValueError('Missing LUT site')
            if n in old and {k:v for k,v in old[n].items() if k not in ('type','parameters','port_directions','connections')}!={k:v for k,v in c.items() if k not in ('type','parameters','port_directions','connections')}:raise ValueError('Changed LUT metadata')
        if set(layout['clusters'])!={n for n,c in new.items() if c['type'] in LUTS}:raise ValueError('LUT placement membership differs')
        stable=lambda rows:[r for r in rows if r.get('kind')!='lut_truth_independent_constant_input']
        if stable(self.static)!=stable(static):raise ValueError('Changed dedicated/static physical links')

    def rebuild(self,design,layout,logical,measurements,static):
        self.admit(design,layout,static)
        cells=design['modules']['top']['cells'];constants={'0':0,'1':1};drivers={};inputs={};locations={};owners={}
        clocks={bit for c in cells.values() if c['type'].startswith('DFF') for bit in c['connections'].get('CLK',[])}
        for n,c in cells.items():
            for p,bit in pins(c,'output'):
                if bit in drivers:raise ValueError('Multiple graph drivers')
                drivers[bit]=n+'/'+p
                if c['type'] in ('GND','VCC'):constants[bit]=int(c['type']=='VCC')
            inputs.update({n+'/'+p:bit for p,bit in pins(c,'input')})
        wanted={e['destination'] for mode in self.graphs.values() for e in mode['edges'] if e['kind']=='wire'}
        wanted.update(n+'/'+p for n,c in cells.items() if c['type'] in LUTS for p,_ in pins(c,'input'))
        for row in logical:
            key=row['instance']+'/'+row['pin'];value=(row['net'],row['node'])
            if key not in wanted:continue
            if key in locations and locations[key]!=value:raise ValueError('Ambiguous physical timing input '+key)
            locations[key]=value
        for net,values in measurements.items():
            for resource in values:
                if resource in owners and owners[resource]!=net:raise ValueError('Measured trees share a physical receiver')
                owners[resource]=net
        def wire(destination,mode,old=None):
            bit=inputs[destination]
            if bit not in drivers:raise ValueError('Undriven graph input')
            source=drivers[bit]
            if destination in self.paired_data:
                if old is None or source!=self.paired_data[destination] or old['source']!=source or old['delay']!=[0.,0.]:raise ValueError('Changed paired data link')
                delay=[0.,0.]
            elif bit in clocks:
                if old is None or old['source']!=source:raise ValueError('Changed fixed clock wire')
                delay=list(old['delay'])
            elif destination in locations:
                net,resource=locations[destination]
                if net not in measurements or resource not in measurements[net]:raise ValueError('Missing complete measured tree for '+destination)
                values=measurements[net][resource];delay=list(values[2:4] if mode=='hold' else values[:2])
            else:
                if old is None or old['source']!=source:raise ValueError('Changed unmeasured dedicated wire')
                delay=list(old['delay'])
            return dict(source=source,destination=destination,delay=delay,transitions=[(0,0),(1,1)],kind='wire')
        graphs={}
        for mode,early in (('setup',False),('hold',True)):
            edges=[];nodes=set(self.graphs[mode]['seeds'])|set(self.model['hold_requirements' if early else 'setup_requirements'])
            for e in self.graphs[mode]['edges']:
                src=e['source'].rsplit('/',1)[0];dst=e['destination'].rsplit('/',1)[0]
                if e['kind']=='wire':
                    if self.reference['modules']['top']['cells'][dst]['type'] in LUTS:continue
                    edges.append(wire(e['destination'],mode,e))
                elif self.reference['modules']['top']['cells'][src]['type'] not in LUTS and self.reference['modules']['top']['cells'][dst]['type'] not in LUTS:edges.append(copy.deepcopy(e))
            for n,c in cells.items():
                if c['type'] not in LUTS:continue
                width=int(c['type'][-1]);bits=[c['connections']['I'+str(i)][0] for i in range(width)];functional=senses(int(c['parameters']['INIT'],2),width,{i:constants[b] for i,b in enumerate(bits) if b in constants})
                nodes.add(n+'/F')
                for i,bit in enumerate(bits):
                    if bit in constants:continue
                    destination=n+'/I'+str(i);edges.append(wire(destination,mode))
                    delay=self.library.lut_input(i,layout['clusters'][n]['slot'],2 if early else 1)
                    edges.append(dict(source=destination,destination=n+'/F',delay=list(delay[:2] if early else delay[2:4]),transitions=[(0,0),(0,1),(1,0),(1,1)],kind='cell',functional_transitions=functional.get(i,[])))
            for e in edges:nodes.update((e['source'],e['destination']))
            graphs[mode]=dict(nodes=sorted(nodes),edges=edges,seeds=copy.deepcopy(self.graphs[mode]['seeds']))
        return graphs

    def metrics(self,graphs):
        return {name:analyze(graphs[name],self.model['hold_requirements' if early else 'setup_requirements'],early) for name,early in (('setup',False),('hold',True))}
