"""Whole rise/fall setup/hold feedback for fixed-data clock alternatives.

Only independently captured positive-edge FF clocks are admitted. Ideal shifts
are opportunity bounds, never native frequency results or realizable routes.
"""
import copy,math
from ownpr_bidir_timing import analyze
from ownpr_ff_configuration import FF_CONTROLS


class ClockDataModel:
    def __init__(self,model,design,clock_capture):
        self.model=model;cells=design['modules']['top']['cells'];self.ff={n for n,c in cells.items() if c['type'] in FF_CONTROLS};self.clocks={};self.base={}
        bits={tuple(cells[n]['connections']['CLK']) for n in self.ff}
        if len(bits)!=1 or len(next(iter(bits)))!=1:raise ValueError('Exactly one scalar FF clock required')
        clock=next(iter(bits))
        pads=[n for n,c in cells.items() if c['type']=='IBUF' and tuple(c['connections']['O'])==clock]
        if len(pads)!=1:raise ValueError('Clock must be driven directly by one primary IBUF; inverted/generated clocks unsupported')
        for n in self.ff:
            params=cells[n].get('parameters',{});default=int(cells[n]['type'] in ('DFFSE','DFFPE'));value=params.get('INIT',str(default))
            if set(params)-{'INIT'} or not isinstance(value,str) or not value or set(value)-{'0','1'} or int(value,2)!=default:raise ValueError('Unmodeled FF parameters')
        self.primary_clock_seed=pads[0]+'/O'
        if any(e['source']==self.primary_clock_seed for key in ('late','early') for e in model[key]['edges']):raise ValueError('Primary clock used as timed data requires waveform-aware constraints')
        for row in clock_capture:
            if row['pin']!='CLK' or row['instance'] not in self.ff:continue
            if len(row['nodes'])!=1:raise ValueError('Ambiguous clock capture')
            node=row['nodes'][0];key=tuple(node['node']);values=node['wire_delays']
            if key in self.base and self.base[key]!=values:raise ValueError('Conflicting shared clock query')
            if row['instance'] in self.clocks:raise ValueError('Duplicate FF clock capture')
            self.clocks[row['instance']]=key;self.base[key]=values
        if set(self.clocks)!=self.ff:raise ValueError('Incomplete FF clock coverage')
        for key in ('late','early'):
            if any(n+'/Q' not in model[key]['seeds'] for n in self.ff):raise ValueError('Unmodeled FF Q launch')
            for n in model[key]['seeds']:
                name=n.rsplit('/',1)[0]
                if name not in self.ff and cells[name]['type']!='IBUF':raise ValueError('Unmodeled hard/IO clock source')
        self.baseline=self.evaluate({n:(0.,0.) for n in self.ff})

    def evaluate(self,deltas):
        shifted=self.shifted(deltas);result={}
        for mode,key,early in (('setup','late',False),('hold','early',True)):
            metrics=analyze(shifted[key],shifted[mode+'_requirements'],early)
            result[mode]=dict(wns=metrics['wns'],tns=metrics['tns'],violations=metrics['violations'],endpoints=metrics['endpoints'],transition_slack={n:metrics['slack'][n] for n in shifted[mode+'_requirements']})
        return result

    def shifted(self,deltas):
        if set(deltas)!=self.ff or any(len(v)!=2 or any(not math.isfinite(x) for x in v) for v in deltas.values()):raise ValueError('Incomplete or nonfinite clock deltas')
        result={}
        for mode,key,early,index in (('setup','late',False,0),('hold','early',True,1)):
            graph=dict(self.model[key],seeds=copy.deepcopy(self.model[key]['seeds']));terms=copy.deepcopy(self.model[mode+'_requirements'])
            for name,values in deltas.items():graph['seeds'][name+'/Q']=[v+values[index] for v in graph['seeds'][name+'/Q']]
            for point,values in terms.items():
                name=point.rsplit('/',1)[0]
                if name in deltas:terms[point]=[v+deltas[name][index] if abs(v)<1e9 else v for v in values]
            result[key]=graph;result[mode+'_requirements']=terms
        return result

    def ideal(self,shift):return self.evaluate({n:(shift,shift) for n in self.ff})

    def measured(self,rows):
        return self.evaluate(self.deltas(rows))

    def deltas(self,rows):
        values={tuple(r['node']):r['delays_by_selector'] for r in rows}
        if len(values)!=len(rows) or set(values)!=set(self.base):raise ValueError('Clock path endpoint set changed or duplicated')
        for v in values.values():
            if len(v)!=3 or any(len(row)!=4 or any(not math.isfinite(x) or x<0 for x in row) for row in v):raise ValueError('Invalid native clock measurement')
        return {n:(values[key][1][2]-self.base[key][1][2],values[key][2][0]-self.base[key][2][0]) for n,key in self.clocks.items()}
