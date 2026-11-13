"""Actual-cell CEC across named, unchanged state/hard-resource boundaries.

Signals may be renumbered by Yosys. Boundaries are matched by module-port and
hard-instance/pin identity, not numeric wire IDs. Every hard input is observed;
primitive type, parameters, port shape and FF initial state must agree.
"""
from pathlib import Path
from register_control_map import logic
from map_resource_json import FUNCTIONS
from gw5a_functional_map import blif
from run_abc_reproduction import ROOT,cec


def function(cell):
    if logic(cell):return logic(cell)
    if cell['type'] in FUNCTIONS:
        pins,fun=FUNCTIONS[cell['type']]
        return ([cell['connections'][p][0] for p in pins],cell['connections']['Y'][0],
                sum(int(fun(*[a>>i&1 for i in range(len(pins))]))<<a for a in range(1<<len(pins))))
    if cell['type'] in ('MUX2_LUT7','MUX2_LUT8','MUX2'):
        p=cell['connections'];return [p[k][0] for k in ('I0','I1','S0')],p['O'][0],0xCA
    if cell['type']=='INV':return cell['connections']['I'],cell['connections']['O'][0],1
    if cell['type'] in ('GND','VCC'):
        return [],cell['connections']['G' if cell['type']=='GND' else 'V'][0],int(cell['type']=='VCC')
    return None


def functions(cell):
    if cell['type']!='ALU':
        f=function(cell);return [f] if f else []
    mode=int(cell.get('parameters',{}).get('ALU_MODE','0'),2)
    if mode not in range(9):raise ValueError('Unmodeled native ALU mode')
    sums=carry=0
    for v in range(16):
        a,b,s,ci=[v>>i&1 for i in range(4)]
        propagate=[a^b,a^(1-b),(a^b) if s else a^(1-b),a^(1-b),a^(1-b),(1-a)^b,a,1-a,a if s else 1-a][mode]
        generate=1 if mode in (3,7) else 0 if mode==6 else b if mode==5 else a
        sums|=(propagate^ci)<<v;carry|=(ci if propagate else generate)<<v
    p=cell['connections'];ins=[p[k][0] for k in ('I0','I1','I3','CIN')]
    return [(ins,p['SUM'][0],sums),(ins,p['COUT'][0],carry)]


def initial_values(module):
    result={}
    for r in module.get('netnames',{}).values():
        value=r.get('attributes',{}).get('init')
        if value is not None:
            for b,v in zip(r['bits'],reversed(value)):
                if b in result and result[b]!=v:raise ValueError('Conflicting state initialization')
                result[b]=v
    return result


def initial(values,cell,bit):
    if bit in values:return values[bit]
    if cell['type'].startswith('DFF'):
        return cell.get('parameters',{}).get('INIT','1' if cell['type'] in ('DFFSE','DFFPE') else '0')[-1]
    return None


def prove(before,after,out,timeout=300):
    out=Path(out).resolve();out.mkdir(parents=True);reports=[]
    for mn,old in before['modules'].items():
        if int(old.get('attributes',{}).get('blackbox','0'),2) or not old.get('cells'):continue
        new=after['modules'][mn]
        if old==new:continue
        hard=[{n:c for n,c in m['cells'].items() if c['type']!='$scopeinfo' and not functions(c)} for m in (old,new)]
        initials=[initial_values(m) for m in (old,new)]
        if set(hard[0])!=set(hard[1]):raise ValueError('Named hard/state instance set changed: '+mn)
        ports=lambda m:{n:(r['direction'],len(r['bits'])) for n,r in m.get('ports',{}).items()}
        if ports(old)!=ports(new):raise ValueError('Module port contract changed')
        inputs=[];observations=[]
        for name,row in old.get('ports',{}).items():
            pairs=list(zip(row['bits'],new['ports'][name]['bits']))
            if row['direction'] in ('input','inout'):inputs.extend(pairs)
            if row['direction']!='input':observations.extend(pairs)
        for name,row in old.get('netnames',{}).items():
            keep=row.get('attributes',{}).get('keep','0')
            if not (keep if isinstance(keep,int) else int(keep,2)):continue
            retained=new.get('netnames',{}).get(name)
            if retained is None or len(row['bits'])!=len(retained['bits']):
                raise ValueError('Kept internal signal contract changed: '+name)
            flag=retained.get('attributes',{}).get('keep','0')
            if not (flag if isinstance(flag,int) else int(flag,2)):
                raise ValueError('Kept internal signal attribute removed: '+name)
            observations.extend(zip(row['bits'],retained['bits']))
        for name,c in hard[0].items():
            d=hard[1][name]
            if any(c.get(k,{})!=d.get(k,{}) for k in ('type','parameters','port_directions')):
                raise ValueError('Hard/state function or parameters changed: '+name)
            if set(c['connections'])!=set(d['connections']):raise ValueError('Hard/state pin contract changed')
            for p,bs in c['connections'].items():
                ds=d['connections'][p]
                if len(bs)!=len(ds):raise ValueError('Hard/state pin width changed')
                for a,b in zip(bs,ds):
                    if c['port_directions'][p] in ('output','inout'):
                        if initial(initials[0],c,a)!=initial(initials[1],d,b):raise ValueError('Hard/state initialization changed')
                        inputs.append((a,b))
                    if c['port_directions'][p]!='output':observations.append((a,b))
        bindings=[{},{}];names=[]
        for a,b in inputs:
            if not isinstance(a,int) or not isinstance(b,int):
                if a!=b:raise ValueError('Constant hard output/input-port contract changed')
                continue
            old_name,new_name=bindings[0].get(a),bindings[1].get(b)
            if old_name or new_name:
                if old_name!=new_name:raise ValueError('Boundary alias relationship changed')
            else:
                name='input_'+str(len(names));names.append(name);bindings[0][a]=name;bindings[1][b]=name
        def network(m,side):
            def signal(b):
                if b in ('0','1'):return 'c'+b
                if b in ('x','z'):raise ValueError('Undefined boundary needs explicit normalization')
                return bindings[side].get(b,'internal_'+str(b))
            nodes=[];driven=set()
            for c in m['cells'].values():
                for ins,o,t in functions(c):
                    if not isinstance(o,int):continue
                    nodes.append(dict(inputs=[signal(b) for b in ins],output=signal(o),truth=t));driven.add(signal(o))
            outputs=[]
            for i,pair in enumerate(observations):
                name='observe_'+str(i);nodes.append(dict(inputs=[signal(pair[side])],output=name,truth=2));outputs.append(name)
            nodes.extend([dict(inputs=[],output='c0',truth=0),dict(inputs=[],output='c1',truth=1)])
            unknown={p for n in nodes for p in n['inputs']}-driven-set(names)-{'c0','c1'}
            if unknown:raise ValueError('Unbound native combinational input: '+str(sorted(unknown)[:3]))
            return dict(inputs=names,outputs=outputs,nodes=nodes)
        p=out/('module_'+str(len(reports)));p.mkdir();a,b=p/'before.blif',p/'after.blif'
        a.write_text(blif(network(old,0)));b.write_text(blif(network(new,1)))
        result=cec(ROOT/'build/abc/_TEST/abc.exe',a,b,p/'cec.log',timeout)
        if not result['equivalent']:raise ValueError('Native hard-boundary actual-cell CEC failed: '+mn)
        reports.append(dict(module=mn,hard_instances=len(hard[0]),observed_hard_inputs_and_ports=len(observations),cec=result))
    return reports
