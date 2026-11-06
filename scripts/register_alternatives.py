"""Non-destructive register covers with exact live-area recovery.

Keep the original FF pin implementation as one choice. Add an equivalent full
next-state data cover as an auxiliary Boolean output before cut mapping. After
mapping choose by pin-aware delay and final live LUT area, then recover unused
covers. The vendor synthesized netlist is never an input to this algorithm.
"""
from collections import defaultdict
import copy
import hashlib
import json
import math
from pathlib import Path
from register_control_map import logic


def add(design):
    design=copy.deepcopy(design);records=[]
    for mn,m in design['modules'].items():
        if mn.startswith('~') or int(m.get('attributes',{}).get('blackbox','0'),2):continue
        cells=m.get('cells',{});nets=m.setdefault('netnames',{})
        bits=[b for c in cells.values() for bs in c['connections'].values() for b in bs]
        bits += [b for r in list(nets.values())+list(m.get('ports',{}).values()) for b in r['bits']]
        maximum=max((b for b in bits if isinstance(b,int)),default=1);counter=0
        constants={b:'1' if c['type']=='VCC' else '0' for c in cells.values() if c['type'] in ('VCC','GND') for b in c['connections']['V' if c['type']=='VCC' else 'G']}
        def const(b):return constants.get(b,b)
        def mux(a,b,s,created):
            nonlocal maximum,counter
            a,b,s=const(a),const(b),const(s)
            if s=='0' or a==b:return a
            if s=='1':return b
            maximum+=1;counter+=1;name='ng_register_choice_'+str(counter)
            if name in cells:raise ValueError('Register choice namespace collision')
            c=dict(type='$_MUX_',parameters={},attributes={},connections=dict(A=[a],B=[b],S=[s],Y=[maximum]),port_directions=dict(A='input',B='input',S='input',Y='output'))
            cells[name]=c;created.append(name);return maximum
        for name,ff in list(cells.items()):
            if ff['type'] not in ('DFFRE','DFFSE','DFFCE','DFFPE') or int(ff.get('attributes',{}).get('keep','0'),2):continue
            pin={'DFFRE':'RESET','DFFSE':'SET','DFFCE':'CLEAR','DFFPE':'PRESET'}[ff['type']]
            p=ff['connections'];d,ce,r,q=[const(p[k][0]) for k in ('D','CE',pin,'Q')]
            if ce=='1' and r=='0':continue
            if pin in ('CLEAR','PRESET') and r!='0':continue
            created=[];value=mux(q,d,ce,created)
            value=mux(value,'1' if ff['type'] in ('DFFSE','DFFPE') else '0',r,created)
            alternative=copy.deepcopy(ff);alternative['connections'].update(D=[value],CE=['1'],**{pin:['0']})
            marker='ng_register_alternative_'+str(len(records))
            if marker in nets:raise ValueError('Register choice marker collision')
            nets[marker]=dict(hide_name=0,bits=[value],attributes={'keep':'1','ng_register_alternative':'1'})
            # Verify the actual generic mux graph and candidate FF equation.
            symbols=list(dict.fromkeys(b for b in (d,ce,r,q) if b not in ('0','1')))
            for a in range(1<<len(symbols)):
                vals={b:a>>i&1 for i,b in enumerate(symbols)};vals.update({'0':0,'1':1})
                for n in created:
                    ps=cells[n]['connections'];vals[ps['Y'][0]]=vals[ps['B'][0]] if vals[ps['S'][0]] else vals[ps['A'][0]]
                expected=int(ff['type'] in ('DFFSE','DFFPE')) if vals[r] else vals[d] if vals[ce] else vals[q]
                if vals[value]!=expected:raise AssertionError('Register alternative next-state proof failed')
            records.append(dict(module=mn,name=name,original=copy.deepcopy(ff),alternative=alternative,marker=marker,proof_cases=1<<len(symbols)))
    return design,dict(schema='register-alternatives-v1',choices=records,
                       proof='Actual mux next-state truth; unchanged FF type, clock, Q, INIT, with inactive asynchronous controls.')


def select(design,record,area_allowance=0.05,control_penalty_ps=0):
    if isinstance(control_penalty_ps,bool) or not isinstance(control_penalty_ps,(int,float)) or not math.isfinite(control_penalty_ps) or not 0<=control_penalty_ps<=2000:
        raise ValueError('Register control routing penalty must be finite, from 0 to 2000 ps')
    design=copy.deepcopy(design);reports=[]
    root=Path(__file__).resolve().parents[1]
    wire=json.loads((root/'configs/gw5a_arch_cost_v2.json').read_text())['cut_wire_ps']
    model_path=root/'results/timing/gowin138_model_ff_checked.json'
    model=json.loads(model_path.read_text())
    data_setup=model['ff_data_constraints']['setup']['envelope_ps']
    clock_to_q=math.ceil(model['ff_clock_to_q']['setup_max_ns']*1000)
    modules=defaultdict(list)
    for r in record['choices']:modules[r['module']].append(r)
    for mn,alternatives in modules.items():
        m=design['modules'][mn];cells=m['cells'];nets=m.get('netnames',{})
        for r in alternatives:
            if cells[r['name']]!=r['original']:raise ValueError('Register choice source state changed during mapping')
            if nets[r['marker']]['attributes'].get('ng_register_alternative')!='1':raise ValueError('Invalid alternative marker')
            del nets[r['marker']]
        comb={n:c for n,c in cells.items() if logic(c) or c['type'] in ('VCC','GND')}
        drivers={b:n for n,c in comb.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
        refs=defaultdict(int);live=set();area_count=0
        def ref(bit):
            nonlocal area_count
            stack=[bit]
            while stack:
                b=stack.pop()
                if b in ('0','1'):continue
                refs[b]+=1
                if refs[b]!=1 or b not in drivers:continue
                n=drivers[b];live.add(n);area_count+=int(comb[n]['type'].startswith('LUT'))
                for p,bs in comb[n]['connections'].items():
                    if comb[n]['port_directions'][p]=='input':stack.extend(bs)
        def deref(bit):
            nonlocal area_count
            stack=[bit]
            while stack:
                b=stack.pop()
                if b in ('0','1'):continue
                if refs[b]<=0:raise AssertionError('Negative live reference count')
                refs[b]-=1
                if refs[b] or b not in drivers:continue
                n=drivers[b];live.remove(n);area_count-=int(comb[n]['type'].startswith('LUT'))
                for p,bs in comb[n]['connections'].items():
                    if comb[n]['port_directions'][p]=='input':stack.extend(bs)
        for n,c in cells.items():
            if n not in comb:
                for p,bs in c['connections'].items():
                    if c.get('port_directions',{}).get(p)=='input':
                        for b in bs:ref(b)
        for r in m.get('ports',{}).values():
            if r['direction']!='input':
                for b in r['bits']:ref(b)
        for r in nets.values():
            if int(r.get('attributes',{}).get('keep','0'),2):
                for b in r['bits']:ref(b)
        for n,c in comb.items():
            attrs=c.get('attributes',{})
            if int(attrs.get('keep','0'),2) and not int(attrs.get('ng_generated','0'),2):
                for p,bs in c['connections'].items():
                    if c['port_directions'][p]=='output':
                        for b in bs:ref(b)
        def area():return area_count
        baseline=area();cap=baseline+math.floor(baseline*area_allowance)
        arrival={'0':0.0,'1':0.0};inactive={'0','1'}
        for r in m.get('ports',{}).values():
            if r['direction']=='input':arrival.update({b:0.0 for b in r['bits']})
        todo={}
        for n,c in cells.items():
            if logic(c):todo[n]=c
            else:
                for p,bs in c['connections'].items():
                    if c.get('port_directions',{}).get(p)=='output':
                        for b in bs:
                            arrival[b]=0.0 if c['type'] in ('VCC','GND') else clock_to_q
                            if c['type'] in ('VCC','GND'):inactive.add(b)
        def incoming(b):return arrival[b]+(0 if b in inactive else wire)
        while todo:
            ready=[]
            for n,c in todo.items():
                ins,out,tt=logic(c)
                if not all(b in arrival for b in ins):continue
                if all(b in inactive for b in ins):arrival[out]=0.0;inactive.add(out)
                else:arrival[out]=max(arrival[ins[0]]+200,arrival[ins[1]]+200,incoming(ins[2])+250) if c['type'].startswith('MUX') else max(incoming(b) for b in ins)+521
                ready.append(n)
            if not ready:raise ValueError('Invalid combinational register choice graph')
            for n in ready:del todo[n]
        def sink(ff):
            values=[]
            for p in ('D','CE','RESET','SET','CLEAR','PRESET'):
                if p not in ff['connections']:continue
                b=ff['connections'][p][0]
                if b not in inactive:values.append(arrival[b]+wire+(data_setup if p=='D' else 335+control_penalty_ps))
            return max(values,default=0.0)
        chosen=[]
        for r in sorted(alternatives,key=lambda r:-sink(r['original'])):
            old,new=r['original'],r['alternative'];before,after=sink(old),sink(new)
            if after>before-1e-6:continue
            pins=[p for p in old['connections'] if p not in ('CLK','Q') and old['connections'][p]!=new['connections'][p]]
            for p in pins:
                for b in new['connections'][p]:ref(b)
            for p in pins:
                for b in old['connections'][p]:deref(b)
            if area()>cap:
                for p in pins:
                    for b in old['connections'][p]:ref(b)
                for p in pins:
                    for b in new['connections'][p]:deref(b)
                continue
            cells[r['name']]=copy.deepcopy(new);chosen.append(dict(name=r['name'],old_setup_ps=before,new_setup_ps=after))
        for n in set(comb)-live:del cells[n]
        reports.append(dict(module=mn,baseline_live_luts=baseline,final_live_luts=area(),cap=cap,alternatives=len(alternatives),selected=chosen))
    return design,dict(schema='register-alternative-selection-v1',modules=reports,selected=sum(len(r['selected']) for r in reports),
                       timing_costs=dict(data_setup_ps=data_setup,clock_to_q_ps=clock_to_q,control_setup_proxy_ps=335,
                           control_routing_penalty_ps=control_penalty_ps,
                           model_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
                           scope='Audited rising DFFRE data envelope. Other FF variants and CE/reset use synthesis proxies; native setup/hold signoff is required.'),
                       objective='Minimize analytical FF setup delay under final live LUT budget; original FF implementation always eligible')
