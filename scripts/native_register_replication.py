"""Bounded, initialized synchronous FF replication (experimental, no QoR claim).

Only user-module DFFRE/DFFSE cells are considered. Equal constant INIT and
identical clock/control/data imply equal state at every cycle. An independent
SDK transition proof and exact graph collapse are required before emission.
No general retiming, asynchronous FFs or undefined initial state are admitted.
"""
from collections import defaultdict
import copy
import math
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from dsp_output_register_pack import kept
from gowin_netlist_format import LIBRARY,EXPECTED
from verific_bridge import units
from yosys_runtime import run,quote
from run_abc_reproduction import sha


def initial(cell):
    return cell['parameters'].get('INIT','1' if cell['type']=='DFFSE' else '0')


def preserved_state(attrs):
    if kept(attrs):return True
    for name,value in attrs.items():
        if name.lower() in ('async_reg','syn_async_reg','synchronizer_identification'):
            if str(value).lower() not in ('0','false','no','off','never',''):
                return True
    return False


def replicate(source,limit=48,max_groups=8,area_allowance=.005):
    if limit<1 or max_groups<2 or not 0<=area_allowance<=.02:
        raise ValueError('Invalid replication bounds')
    design=copy.deepcopy(source);records=[]
    for mn,m in design['modules'].items():
        if mn.startswith('~') or int(m.get('attributes',{}).get('blackbox','0'),2):continue
        cells=m.get('cells',{});loads=defaultdict(list)
        for n,c in cells.items():
            for p,bs in c['connections'].items():
                if c['port_directions'].get(p) in ('input','inout'):
                    for i,b in enumerate(bs):loads[b].append((n,p,i))
        protected={b for r in m.get('ports',{}).values() for b in r['bits']}
        protected.update(b for r in m.get('netnames',{}).values() if preserved_state(r.get('attributes',{})) for b in r['bits'])
        ff_count=sum(c['type'].startswith('DFF') for c in cells.values())
        budget=math.floor(ff_count*area_allowance)
        maximum=max((b for r in list(m.get('netnames',{}).values())+list(m.get('ports',{}).values()) for b in r['bits'] if isinstance(b,int)),default=1)
        maximum=max(maximum,max((b for c in cells.values() for bs in c['connections'].values() for b in bs if isinstance(b,int)),default=1))
        names=sorted((n for n,c in cells.items() if c['type'] in ('DFFRE','DFFSE')),
                     key=lambda n:(-len(loads[cells[n]['connections']['Q'][0]]),n))
        for n in names:
            if not budget:break
            c=cells[n];q=c['connections']['Q'][0];sinks=list(loads[q])
            if q in protected or preserved_state(c.get('attributes',{})) or len(sinks)<=2*limit:continue
            if set(c['parameters'])-{'INIT'} or initial(c) not in ('0','1'):continue
            if any(v in ('0','1') and v!=initial(c) for r in m.get('netnames',{}).values()
                   for b,v in zip(r['bits'],reversed(r.get('attributes',{}).get('init','')))
                   if b==q):continue
            if set(c['connections'])!={'D','Q','CLK','CE','RESET' if c['type']=='DFFRE' else 'SET'}:continue
            if any(len(bs)!=1 for bs in c['connections'].values()):continue
            # Clock trees, asynchronous control and hard placement ownership
            # are outside this experiment's contract.
            if any(cells[child]['port_directions'][p]!='input' or
                   p.upper().startswith(('CLK','CLOCK')) or p.upper() in ('CLEAR','PRESET') or
                   (p.upper() in ('RESET','SET') and cells[child]['type'] not in ('DFFRE','DFFSE')) or
                   (cells[child]['type'].startswith('MUX2_LUT') and p in ('I0','I1'))
                   for child,p,_ in sinks):continue
            count=min(math.ceil(len(sinks)/limit),max_groups,budget+1)
            ordered=sorted(sinks,key=lambda r:(r[0].split('.ng_')[0],r[0],r[1],r[2]))
            size=math.ceil(len(ordered)/count);groups=[ordered[i:i+size] for i in range(0,len(ordered),size)]
            loads[q]=groups[0]
            for j,group in enumerate(groups[1:],1):
                name=n+'_ng_state_replica_'+str(j)
                if name in cells:raise ValueError('Replica namespace collision')
                maximum+=1;clone=copy.deepcopy(c);clone['connections']['Q']=[maximum]
                clone.setdefault('attributes',{}).update(keep='1',ng_generated='1')
                cells[name]=clone
                for p,bs in clone['connections'].items():
                    if clone['port_directions'][p]=='input':
                        for i,b in enumerate(bs):loads[b].append((name,p,i))
                for child,p,i in group:
                    if cells[child]['connections'][p][i]!=q:raise ValueError('Stale sink partition')
                    cells[child]['connections'][p][i]=maximum
                loads[maximum]=group
                records.append(dict(module=mn,source=n,clone=name,old_q=q,new_q=maximum,
                    original_fanout=len(sinks),sinks=len(group),init=initial(c)))
                budget-=1
    return design,dict(schema='initialized-sync-ff-replication-v1',copies=records,clones=len(records),
        limit=limit,max_groups=max_groups,area_allowance=area_allowance,
        scope=__doc__,implementation_sha256=sha(Path(__file__)),model_sha256=EXPECTED)


def validate_graph(before,after,record):
    """Collapse independently checked equal-state replicas; demand exact graph."""
    reduced=copy.deepcopy(after);aliases=defaultdict(dict);names=set()
    for row in record['copies']:
        mn,n,cn=row['module'],row['source'],row['clone']
        if (mn,cn) in names:raise ValueError('Duplicate replica certificate')
        names.add((mn,cn))
        original=before['modules'][mn]['cells'][n];clone=after['modules'][mn]['cells'][cn]
        if preserved_state(original.get('attributes',{})):raise ValueError('Preserved state cannot be replicated')
        if original['type'] not in ('DFFRE','DFFSE') or initial(original) not in ('0','1'):
            raise ValueError('Unproved state semantics')
        if set(original['parameters'])-{'INIT'} or initial(original)!=row['init'] or clone['parameters']!=original['parameters']:
            raise ValueError('Replica initialization changed')
        if original['connections']['Q']!=[row['old_q']] or row['new_q'] in aliases[mn]:
            raise ValueError('Invalid state correspondence')
        aliases[mn][row['new_q']]=row['old_q']
        del reduced['modules'][mn]['cells'][cn]
    for row in record['copies']:
        mn=row['module'];clone=copy.deepcopy(after['modules'][mn]['cells'][row['clone']])
        for p,bs in clone['connections'].items():
            if p!='Q':clone['connections'][p]=[aliases[mn].get(b,b) for b in bs]
        expected=copy.deepcopy(before['modules'][mn]['cells'][row['source']])
        expected['connections']['Q']=[row['new_q']];expected.setdefault('attributes',{}).update(keep='1',ng_generated='1')
        if clone!=expected:raise ValueError('Replica function/control changed')
    for mn,m in reduced['modules'].items():
        for c in m.get('cells',{}).values():
            for p,bs in c['connections'].items():c['connections'][p]=[aliases[mn].get(b,b) for b in bs]
    if reduced!=before:raise ValueError('Changes beyond equal-state replication')
    return dict(status='exact_graph_collapse_passed',clones=len(names))


def collapse_emitted(design,record):
    """Check actual emitted source/copy state, then collapse equal-state Qs."""
    result=copy.deepcopy(design);aliases=defaultdict(dict);seen=set()
    for row in record['copies']:
        mn=row['module'];m=result['modules'][mn];a=m['cells'][row['source']];b=m['cells'][row['clone']]
        if (mn,row['clone']) in seen:raise ValueError('Duplicate emitted copy record')
        seen.add((mn,row['clone']))
        if a['type'] not in ('DFFRE','DFFSE') or initial(a)!=row['init'] or initial(a) not in ('0','1'):
            raise ValueError('Emitted source initialization differs')
        if any(a[k]!=b[k] for k in ('type','parameters','port_directions')):
            raise ValueError('Emitted replica metadata differs')
        aliases[mn][b['connections']['Q'][0]]=a['connections']['Q'][0]
    for row in record['copies']:
        mn=row['module'];m=result['modules'][mn];a=m['cells'][row['source']];b=m['cells'][row['clone']]
        norm=lambda bs:[aliases[mn].get(bit,bit) for bit in bs]
        if set(a['connections'])!=set(b['connections']) or any(norm(a['connections'][p])!=norm(bs) for p,bs in b['connections'].items() if p!='Q'):
            raise ValueError('Emitted replica transition differs')
        del m['cells'][row['clone']]
    for mn,m in result['modules'].items():
        for c in m.get('cells',{}).values():
            for p,bs in c['connections'].items():c['connections'][p]=[aliases[mn].get(bit,bit) for bit in bs]
    return result


def prove_sdk(out,negative_control=False):
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    if sha(LIBRARY)!=EXPECTED:raise ValueError('SDK model changed')
    text=LIBRARY.read_text(encoding='utf-8');spans=units(text);body=[]
    for kind in ('DFFRE','DFFSE'):
        a,b=spans[kind];model=text[a:b]
        if model.count('GSR.GSRO')!=1:raise ValueError('Unknown FF global-reset model')
        # Procedural continuous assignment in the SDK is not synthesizable.
        # Replace only its exact global-force block by the equivalent async
        # reset-to-INIT branch; synchronous control still takes precedence
        # over CE. Both models receive the same unconstrained global signal.
        model=model.replace('wire gsrt = GSR.GSRO;','wire gsrt = global_gsr;')
        model=model.replace('CLK, CE, RESET);','CLK, CE, RESET, global_gsr);').replace('CLK, CE, SET);','CLK, CE, SET, global_gsr);')
        model=model.replace('reg Q_reg;','input global_gsr;\nreg Q_reg;')
        pattern=r'always @\(gsrt\)\s*begin\s*if\(!gsrt\)\s*assign Q_reg = INIT;\s*else\s*deassign Q_reg;\s*end'
        model,count=re.subn(pattern,'',model)
        if count!=1 or model.count('always @(posedge CLK) begin')!=1:raise ValueError('Unknown SDK FF transition')
        model=model.replace('always @(posedge CLK) begin','always @(posedge CLK or negedge gsrt) begin\n if (!gsrt) Q_reg <= INIT;\n else')
        body.append(model)
    lines=['module miter(input clk,ce,d,control,global_gsr,output equal);'];equal=[]
    for kind,ctrl in [('DFFRE','RESET'),('DFFSE','SET')]:
        for init in (0,1):
            label=kind+str(init);lines.append('wire '+label+'a,'+label+'b;')
            for side in 'ab':
                enable='~ce' if negative_control and side=='b' else 'ce'
                lines.append(f"{kind} #(.INIT(1'b{init})) {label}{side}_cell(.CLK(clk),.CE({enable}),.D(d),.{ctrl}(control),.Q({label}{side}),.global_gsr(global_gsr));")
            equal.append('('+label+'a=='+label+'b)')
    lines.append('assign equal='+'&'.join(equal)+';endmodule')
    p=out/'proof.v';p.write_text('\n'.join(body+lines)+'\n')
    run(['read_verilog '+quote(p),'hierarchy -top miter','proc','flatten','opt_expr','opt_clean','async2sync','dffunmap',
         'write_json '+quote(out/'transition.json'),
         'sat -tempinduct -seq 2 -maxsteps 3 -prove equal 1 -verify -timeout 30'],out,'proof',45)
    if 'SUCCESS!' not in (out/'proof.log').read_text():raise ValueError('State replication proof failed')
    transition=json.loads((out/'transition.json').read_text())['modules']['miter']
    states=[c for c in transition['cells'].values() if c['type']=='$dff']
    if len(states)!=8 or not isinstance(transition['ports']['equal']['bits'][0],int):
        raise ValueError('Reject vacuous register replication proof')
    return dict(status='proved',model_sha256=EXPECTED,proof_source_sha256=sha(p),proof_log_sha256=sha(out/'proof.log'),
        preserved_state_cells=8,transition_sha256=sha(out/'transition.json'),
        scope='Identical constant INIT and inputs; temporal induction including shared global reset. SDK force-to-INIT projected to synthesizable async-reset form.',negative_control=negative_control)
