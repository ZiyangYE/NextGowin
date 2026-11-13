"""Across-box min/max feedback for alternative ALU/LUT covers.

Hard/state/electrical native contexts stay fixed. Combinational cell arcs use
the calibrated native libraries. Unplaced wires use an explicit learned prior,
so this is mapping feedback, never routed timing or signoff. Retained ALUs are
ordinary vertices here, not invented zero-arrival black-box inputs.
"""
import copy,math
from collections import defaultdict
from native_boundary_cec import functions
from ownpr_bidir_timing import analyze,topology
from ownpr_synthesis_boundary_budgets import minimum_required
from ownpr_timing_pins import pins

COMB={'LUT1','LUT2','LUT3','LUT4','MUX2_LUT5','MUX2_LUT6','MUX2_LUT7','ALU','GND','VCC'}
RELATION=[(0,0),(0,1),(1,0),(1,1)]


def connectivity(cells):
    drivers={};loads=defaultdict(list);constants={'0','1'}
    for n,c in cells.items():
        for p,b in pins(c,'output'):
            if b in drivers:raise ValueError('Multiple heterogeneous drivers')
            drivers[b]=n+'/'+p
            if c['type'] in ('GND','VCC'):constants.add(b)
        for p,b in pins(c,'input'):loads[b].append(n+'/'+p)
    return drivers,loads,constants


def fixed_contract(design,reference,inactive_clocks=frozenset()):
    cells=design['modules']['top']['cells'];old=reference['modules']['top']['cells']
    a={n:c for n,c in cells.items() if c['type'] not in COMB};b={n:c for n,c in old.items() if c['type'] not in COMB}
    if set(a)!=set(b):raise ValueError('Changed named hard/state/IO boundaries')
    drivers,_,_=connectivity(cells);old_drivers,_,_=connectivity(old)
    def primary_inputs(module):
        result=defaultdict(list)
        for name,row in module.get('ports',{}).items():
            if row['direction'] not in ('input','inout'):continue
            for i,bit in enumerate(row['bits']):result[bit].append((name,i+row.get('offset',0)))
        return {bit:tuple(sorted(names)) for bit,names in result.items()}
    primary=primary_inputs(design['modules']['top']);old_primary=primary_inputs(reference['modules']['top'])
    def clock_token(bit,by_bit,table,ports):
        if bit in ('0','1'):return ('constant',int(bit))
        if bit in ports:return ('primary_input',ports[bit])
        point=by_bit.get(bit)
        if point is None:raise ValueError('Missing clock-driver identity')
        typ=table[point.rsplit('/',1)[0]]['type']
        return ('constant',int(typ=='VCC')) if typ in ('GND','VCC') else ('driver',point)
    for n,c in a.items():
        before=b[n]
        if {k:v for k,v in c.items() if k!='connections'}!={k:v for k,v in before.items() if k!='connections'}:raise ValueError('Changed hard/state primitive '+n)
        if set(c['connections'])!=set(before['connections']) or any(len(v)!=len(before['connections'][p]) for p,v in c['connections'].items()):raise ValueError('Changed hard port shape')
        for p in c['connections']:
            if p not in ('CLK','CLKA','CLKB','CLKIN'):continue
            x,y=c['connections'][p],before['connections'][p]
            for index,(left,right) in enumerate(zip(x,y)):
                label=p if len(x)==1 else f'{p}[{index}]'
                if clock_token(left,drivers,cells,primary)!=clock_token(right,old_drivers,old,old_primary):raise ValueError('Changed inactive DSP clock connection' if (n,label) in inactive_clocks else 'Changed clock-driver identity')
    # Full control/data functional equivalence is a separate actual-JSON CEC
    # gate. This admission never declares controls equivalent by signal class.
    return set(a)


def active_required_transitions(graph,terminals):
    # Track provenance of real native requirements, rather than recognizing a
    # numerically shifted1e10 sentinel after reverse delay propagation.
    order,_,outgoing=topology(graph);active={n:[False,False] for n in order}
    for n,values in terminals.items():
        minimum_required(values)
        active[n]=[abs(v)<1e9 for v in values]
    for n in reversed(order):
        for i in outgoing[n]:
            e=graph['edges'][i]
            for a,b in e['transitions']:active[n][a]|=active[e['destination']][b]
    return active


class HeterogeneousTiming:
    def __init__(self,reference,model,lut_library,alu_library,mux_library,wire_ns=.672,fast_wire_ns=.25,wire_prior=None):
        if not math.isfinite(wire_ns) or not math.isfinite(fast_wire_ns) or not 0<=fast_wire_ns<=wire_ns:raise ValueError('Invalid explicit wire prior')
        self.reference=reference;self.model=model;self.lut=lut_library;self.alu=alu_library;self.mux=mux_library;self.wire=wire_ns;self.fast_wire=fast_wire_ns
        self.wire_prior=wire_prior
        self.inactive_clocks=set();self.inactive_clock_evidence=[]
        if any(c['type']=='MULTALU27X18' for c in reference['modules']['top']['cells'].values()):
            from ownpr_hard_architecture import HardArchitecture
            from ownpr_hard_timing_model import profile
            architecture=HardArchitecture();contract=architecture.contract(reference)
            for n,row in contract.items():
                if row['primitive']!='MULTALU27X18':continue
                cell=reference['modules']['top']['cells'][n];key=profile(cell);record=architecture.timing.profiles[key]
                for pin in row['inactive']:
                    if pin['reason']!='bypass_clock':continue
                    point=n+'/'+pin['pin']
                    # The independent native profile must expose the complete
                    # combinational DSP graph and no timed use of this clock.
                    if len(record['edges'])!=1656 or any(a.startswith('CLK') or b.startswith('CLK') for a,b,_ in record['edges']):raise ValueError('DSP profile has clock timing')
                    if any(point in g['nodes'] or point in g['seeds'] for g in (model['late'],model['early'])) or any(point in model[k] for k in ('setup_requirements','hold_requirements')):raise ValueError('Inactive DSP clock is timed in candidate context')
                    self.inactive_clocks.add((n,pin['pin']));self.inactive_clock_evidence.append(dict(instance=n,pin=pin['pin'],reason='independent_native_bypass_profile_without_clock_edges',profile=key,independent_graph_edges=1656))

    def graphs(self,design):
        hard=fixed_contract(design,self.reference,self.inactive_clocks);cells=design['modules']['top']['cells'];drivers,loads,constants=connectivity(cells);graphs={};adapters=set();bridges=set();heads=set()
        for mode,key,early,selector in (('setup','late',False,1),('hold','early',True,2)):
            base=self.model[key];seeds=copy.deepcopy(base['seeds']);terms=self.model[mode+'_requirements'];edges=[]
            if any(n.rsplit('/',1)[0] not in hard for n in seeds):raise ValueError('Unmodeled native launch context')
            if any(n.rsplit('/',1)[0] not in hard for n in terms):raise ValueError('Combinational endpoint requirement not admitted')
            internal=[copy.deepcopy(e) for e in base['edges'] if e['kind']!='wire' and e['source'].rsplit('/',1)[0] in hard and e['destination'].rsplit('/',1)[0] in hard]
            edges.extend(internal);needed=set(terms)|set(seeds)|{p for e in internal for p in (e['source'],e['destination'])}
            native_wires={e['destination']:e for e in base['edges'] if e['kind']=='wire'}
            def component(values):return list(values[:2] if early else values[2:4])
            def wire(destination,bit):
                if bit in constants:return
                source=drivers.get(bit)
                if source is None:raise ValueError('Uncovered heterogeneous input '+destination)
                n,p=destination.rsplit('/',1);src=source.rsplit('/',1)[0];typ=cells[n]['type'];st=cells[src]['type']
                if p in ('CLK','CLKA','CLKB','CLKIN'):
                    old=native_wires.get(destination)
                    if old is None or old['source']!=source:raise ValueError('Uncalibrated hard clock data arc')
                    edges.append(copy.deepcopy(old));return
                d=[self.fast_wire if early else self.wire]*2
                if not early and self.wire_prior is not None:
                    from ownpr_loaded_wire_prior import predict
                    d=[max(self.fast_wire,predict(self.wire_prior,st,len(loads[bit]),self.wire))]*2
                if typ=='ALU' and p=='CIN' and st=='ALU' and source.endswith('/COUT'):d=[0.,0.]
                elif typ=='ALU' and p=='CIN':
                    extra=component(self.alu.alu[2,0,'I1','COUT',selector]);d=[a+b for a,b in zip(d,extra)];heads.add(destination)
                elif typ.startswith('MUX2_LUT') and p in ('I0','I1'):d=[0.,0.]
                elif typ.startswith('DFF') and p=='D':
                    ffloads=[v for v in loads[bit] if cells[v.rsplit('/',1)[0]]['type'].startswith('DFF') and v.endswith('/D')]
                    if st in ('LUT1','LUT2','LUT3','LUT4') and ffloads[0]==destination:d=[0.,0.]
                    elif st=='ALU' and source.endswith('/COUT'):pass
                    else:
                        # Initial packer inserts an explicit identity LUT for
                        # ALU/wide/hard outputs or a second FF on the same LUT.
                        extra=component(self.lut.lut_input(0,0,selector));d=[x+y for x,y in zip(d,extra)];adapters.add(destination)
                if st=='ALU' and source.endswith('/COUT') and not (typ=='ALU' and p=='CIN'):
                    # Terminal dedicated COUT needs ALU(~CIN)+LUT inverter.
                    bridge=component(self.alu.alu[2,0,'CIN','SUM',selector]);inv=component(self.lut.lut_input(0,0,selector))
                    extra_wire=0. if typ.startswith('DFF') and p=='D' else self.fast_wire if early else self.wire
                    d=[a+b+c+extra_wire for a,b,c in zip(d,bridge,inv)];bridges.add(source)
                edges.append(dict(source=source,destination=destination,delay=d,transitions=[(0,0),(1,1)],kind='wire_prior'))
            for n,c in cells.items():
                typ=c['type']
                if typ in ('GND','VCC'):continue
                if typ in COMB:
                    if typ=='ALU':
                        alu_mode=int(c['parameters']['ALU_MODE'],2)
                        if alu_mode!=2:raise ValueError('ALU mode lacks calibrated min/max arcs')
                        arcs=[]
                        for p in ('I0','I1','I3','CIN'):
                            for out in ('SUM','COUT'):
                                values=[self.alu.alu[alu_mode,s,p,out,selector] for s in range(6)]
                                delay=[min(v[i] for v in values) if early else max(v[2+i] for v in values) for i in (0,1)]
                                arcs.append((p,out,delay))
                    elif typ.startswith('LUT'):
                        arcs=[('I'+str(i),'F',[min(self.lut.lut_input(i,s,selector)[j] for s in range(8)) if early else max(self.lut.lut_input(i,s,selector)[2+j] for s in range(8)) for j in (0,1)]) for i in range(int(typ[-1]))]
                    else:
                        level=int(typ[-1]);slot={5:0,6:1,7:3}[level]
                        arcs=[(p,'O',component(self.mux.delay(17,level,slot,p,selector))) for p in ('I0','I1','S0')]
                    for p,out,delay in arcs:
                        if c['connections'][p][0] in constants:continue
                        edges.append(dict(source=n+'/'+p,destination=n+'/'+out,delay=delay,transitions=RELATION,kind='native_cell'))
                    for p,bits in c['connections'].items():
                        if c['port_directions'][p]=='input':wire(n+'/'+p,bits[0])
                else:
                    for p,bit in pins(c,'input'):
                        point=n+'/'+p
                        if point in needed:wire(point,bit)
            nodes=needed|{n for e in edges for n in (e['source'],e['destination'])}
            graphs[mode]=dict(nodes=sorted(nodes),edges=edges,seeds=seeds)
        return graphs,dict(ff_adapter_endpoints=sorted(adapters),terminal_carry_bridge_outputs=sorted(bridges),dynamic_carry_head_inputs=sorted(heads),wire_prior_ns=self.wire,fast_wire_prior_ns=self.fast_wire,loaded_wire_prior=self.wire_prior,native_routed_timing=False)

    def evaluate(self,design):
        graphs,info=self.graphs(design);metrics={mode:analyze(graphs[mode],self.model[mode+'_requirements'],mode=='hold') for mode in ('setup','hold')}
        return graphs,metrics,info

    def mapping_constraints(self,design,network,make_feasible=True):
        graphs,metrics,info=self.evaluate(design);drivers,loads,_=connectivity(design['modules']['top']['cells']);timing=metrics['setup'];arrivals={};required={};witness={}
        active=active_required_transitions(graphs['setup'],self.model['setup_requirements'])
        shift=max(0.,-timing['wns']) if make_feasible else 0.
        for label in network['inputs']:
            bit=int(label[1:]);point=drivers.get(bit)
            if point not in timing['arrival'] or any(not math.isfinite(v) for v in timing['arrival'][point]):raise ValueError('Missing across-box mapper arrival '+label)
            arrivals[label]=math.ceil(max(timing['arrival'][point])*1000);witness[label]=dict(source=point,arrival_ns=timing['arrival'][point])
        for label in network['outputs']:
            bit=int(label[1:]);point=drivers.get(bit)
            if point not in timing['required']:raise ValueError('Missing across-box mapper requirement '+label)
            values=[v for i,v in enumerate(timing['required'][point]) if active[point][i]]
            if not values or any(not math.isfinite(v) or abs(v)>=1e9 for v in values):raise ValueError('Uncovered active across-box output requirement '+label)
            required[label]=math.floor((min(values)+shift)*1000)
        return dict(units='ps',input_arrivals_ps=arrivals,output_required_ps=required),dict(info,optimization_only_uniform_requirement_shift_ns=shift,
            actual_sta_requirements_unchanged=True,source_witnesses=witness,setup={k:metrics['setup'][k] for k in ('wns','tns','violations')},hold={k:metrics['hold'][k] for k in ('wns','tns','violations')},scope=__doc__)
