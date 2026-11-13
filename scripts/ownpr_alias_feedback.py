"""In-memory complete-tree deltas for fixed-clock alias transactions."""
import copy
from ownpr_bidir_timing import analyze
from ownpr_timing_pins import pins


class AliasFeedback:
    def __init__(self,model,captures,graph,reference,aliases):
        self.model=model;self.resources={};self.reference=reference
        cells=reference['modules']['top']['cells'];self.canonical={cells[n]['connections']['Q'][0]:cells[p]['connections']['Q'][0] for n,p in aliases.items()}
        if set(aliases)&set(aliases.values()):raise ValueError('Alias representatives must be roots')
        for r in captures:
            if len(r['nodes'])==1:
                y,x,i=r['nodes'][0]['node']
                if min(y,x)>=1 and i in graph.device.names:self.resources[r['instance']+'/'+r['pin']]=graph.node(y-1,x-1,graph.device.names[i])

    def metrics(self,graphs):
        return {name:analyze(graphs[name],self.model['hold_requirements' if early else 'setup_requirements'],early) for name,early in (('setup',False),('hold',True))}

    def update(self,current,design,measurements):
        owners={}
        for net,values in measurements.items():
            for resource in values:
                if resource in owners and owners[resource]!=net:raise ValueError('Distinct measured nets share a physical receiver')
                owners[resource]=net
        before=self.reference['modules']['top'];after=design['modules']['top']
        if {n:v for n,v in self.reference['modules'].items() if n!='top'}!={n:v for n,v in design['modules'].items() if n!='top'} or {k:v for k,v in before.items() if k!='cells'}!={k:v for k,v in after.items() if k!='cells'} or set(before['cells'])!=set(after['cells']):raise ValueError('Changed timing boundary contract')
        for n,c in before['cells'].items():
            d=after['cells'][n]
            if {k:v for k,v in c.items() if k!='connections'}!={k:v for k,v in d.items() if k!='connections'} or set(c['connections'])!=set(d['connections']):raise ValueError('Changed fixed primitive contract')
            for pin,bs in c['connections'].items():
                ds=d['connections'][pin]
                if bs==ds:continue
                allowed=c['type'] in ('LUT1','LUT2','LUT3','LUT4') and pin.startswith('I') or c['type']=='ALU' and pin in ('I0','I1','I3') or c['type'].startswith('MUX2_LUT') and pin=='S0'
                if not allowed or c['port_directions'][pin]!='input' or len(bs)!=1 or len(ds)!=1 or self.canonical.get(bs[0],bs[0])!=self.canonical.get(ds[0],ds[0]):raise ValueError('Non-alias input or fixed-state change')
        cells=design['modules']['top']['cells'];inputs={n+'/'+p:b for n,c in cells.items() for p,b in pins(c,'input')};drivers={b:n+'/'+p for n,c in cells.items() for p,b in pins(c,'output')};result={}
        for name,offset in (('setup',0),('hold',2)):
            old=current[name];edges=[]
            for e in old['edges']:
                if e['kind']!='wire':edges.append(e);continue
                bit=inputs[e['destination']];net='net_'+str(bit);source=drivers[bit]
                if net not in measurements:
                    if source!=e['source']:raise ValueError('Changed driver without whole-tree measurement')
                    edges.append(e);continue
                sink=self.resources.get(e['destination'])
                if sink not in measurements[net]:raise ValueError('Whole-tree measurement omits an existing receiver')
                values=measurements[net][sink]
                edges.append(dict(e,source=source,delay=list(values[offset:offset+2])))
            result[name]=dict(old,edges=edges)
        return result


def preserves_timing_guards(candidate,current,hold_reference=None):
    reference=(hold_reference or current)['hold'];hold_floor=min(.05,max(0.,reference['wns']))
    if 'endpoints' in reference:
        actual=candidate['hold'].get('endpoints',{})
        if set(actual)!=set(reference['endpoints']) or any(actual[n]<min(.05,max(0.,v))-1e-7 for n,v in reference['endpoints'].items()):return False
    return candidate['hold']['wns']>=hold_floor-1e-7 and not candidate['hold']['violations'] and candidate['setup']['wns']>=current['setup']['wns']-.0005


def admissible(candidate,current,hold_reference=None):
    a,b=candidate['setup'],current['setup']
    return preserves_timing_guards(candidate,current,hold_reference) and (a['wns']>b['wns']+.01 or a['tns']>b['tns']+.1)
