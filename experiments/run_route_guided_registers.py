"""Small, measured-delay-driven FF copies near selected critical LUT loads."""
import copy,json,math,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from yosys_runtime import run,quote
from native_register_replication import initial,preserved_state,validate_graph,collapse_emitted,prove_sdk
from native_boundary_cec import prove
from gowin_netlist_format import name_unaliased_bits,normalize
from routing_path_diagnosis import diagnose
from project_timing import audit
from run_routing_ablation import pins
from flow_budget import Budget,activate

def main():
    out=ROOT/'results/cpu_route_guided_001';out.mkdir(exist_ok=False);budget=Budget(1500);activate(budget)
    base=ROOT/'results/tangprimer138_cpu_002/vexriscv';prior=base/'own/wide';source=prior/'05_netlist/mapped.vg'
    # User preservation metadata was retained in JSON, but not in the .vg.
    meta=json.loads((prior/'05_netlist/final.json').read_text())
    for m in meta['modules'].values():
        for c in m.get('cells',{}).values():
            if c['type'].startswith('DFF') and preserved_state(c.get('attributes',{})) and c.get('attributes',{}).get('ng_generated')!='1':raise ValueError('Cannot map preserved user state to emitted names safely')
    run(['read_verilog -lib +/gowin/cells_sim.v','read_verilog -lib +/gowin/cells_xtra_gw5a.v','read_verilog '+quote(source),'hierarchy -check -top VexRiscv','blackbox =A:whitebox','write_json '+quote(out/'source.json')],out,'read')
    before=json.loads((out/'source.json').read_text());name_unaliased_bits(before);after=copy.deepcopy(before);m=after['modules']['VexRiscv'];cells=m['cells'];records=[];chosen=[];seen=set()
    maximum=max(b for r in m['netnames'].values() for b in r['bits'] if isinstance(b,int))
    paths=diagnose((prior/'06_pnr/mapped.tr').read_text());edges=sorted([e for p in paths for e in p['edges']],key=lambda e:-e['delay_ns'])
    for e in edges:
        if len(records)>=4:break
        src,sp=e['source'].rsplit('/',1);dst,dp=e['destination'].rsplit('/',1)
        if src in seen or src not in cells or dst not in cells or sp!='Q' or e['delay_ns']<2:continue
        c=cells[src];sink=cells[dst]
        if c['type'] not in ('DFFRE','DFFSE') or initial(c) not in ('0','1') or set(c['parameters'])-{'INIT'}:continue
        if not re.fullmatch('LUT[1-4]',sink['type']) or not dp.startswith('I') or e['source_fanout']<=1:continue
        q=c['connections']['Q'][0]
        if sink['connections'][dp]!=[q]:raise ValueError('Timing edge does not match actual graph')
        loc=re.match(r'R(\d+)C(\d+)',e['destination_location'])
        if not loc:continue
        maximum+=1;name='ng_route_copy_'+str(len(records));clone=copy.deepcopy(c);clone['connections']['Q']=[maximum];clone.setdefault('attributes',{}).update(keep='1',ng_generated='1');cells[name]=clone;sink['connections'][dp]=[maximum]
        records.append(dict(module='VexRiscv',source=src,clone=name,old_q=q,new_q=maximum,original_fanout=e['source_fanout'],sinks=1,init=initial(c)))
        chosen.append(dict(edge=e,clone=name,row=int(loc[1]),column=int(loc[2])));seen.add(src)
    if not records:raise ValueError('No eligible critical FF edges')
    record=dict(copies=records,clones=len(records),selected=chosen);record['graph_proof']=validate_graph(before,after,record);record['state_proof']=prove_sdk(out/'state_proof')
    for name,d in [('before',before),('after',after)]:
        name_unaliased_bits(d);(out/(name+'.json')).write_text(json.dumps(d)+'\n')
        run(['read_json '+quote(out/(name+'.json')),'read_verilog -overwrite -lib +/gowin/cells_sim.v','read_verilog -overwrite -lib +/gowin/cells_xtra_gw5a.v','blackbox =A:whitebox','setundef -params -zero','hilomap -singleton -hicell VCC V -locell GND G','clean -purge','splitnets -ports','write_json '+quote(out/(name+'_actual.json')),'write_verilog -noattr -defparam '+quote(out/(name+'_raw.vg'))],out,'emit_'+name)
    a=json.loads((out/'before_actual.json').read_text());b=collapse_emitted(json.loads((out/'after_actual.json').read_text()),record)
    record['actual_cec']=prove(a,b,out/'actual_cec');(out/'proof.json').write_text(json.dumps(record,indent=2)+'\n')
    vg=out/'mapped.vg';text,_=normalize((out/'after_raw.vg').read_text());vg.write_text(text)
    sdc=out/'clock90.sdc';sdc.write_text((base/'clock200.sdc').read_text().replace('-period 5 ','-period 11.111111111 '));cst=out/'placed.cst';text=(base/'fixed.cst').read_text()
    for r in chosen:text+=f'INS_LOC "{r["clone"]}" R[{r["row"]-1}:{r["row"]+1}]C[{r["column"]-1}:{r["column"]+1}];\n'
    cst.write_text(text);results=[]
    for name,net,phys in [('vendor',base/'vendor_synthesis/mapped.vg',base/'fixed.cst'),('own_before',source,base/'fixed.cst'),('own_guided',vg,cst)]:
        folder=out/name
        try:
            with (out/(name+'.log')).open('w') as log:subprocess.run([sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(net),'--sdc',str(sdc),'--cst',str(phys),'--out',str(folder),'--output-iob-registers','--timeout',str(int(budget.timeout(360,reserve=5)))],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=budget.remaining())
            p=audit(folder,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':1000/90}));(folder/'physical_audit.json').write_text(json.dumps(p,indent=2)+'\n')
            if pins(folder/'mapped.pin.html')!=pins(base/'vendor_pnr100/mapped.pin.html'):raise ValueError('Actual I/O mismatch')
            row=dict(name=name,status='complete',frequency=p['frequency_reports'],closed=p['timing_closed'],violations=p['violated_endpoints'],resources=p['implementation']);print(row,flush=True)
        except Exception as e:row=dict(name=name,status='failed',error=repr(e));print(row,flush=True)
        results.append(row);(out/'manifest.json').write_text(json.dumps(dict(status='running',copies=len(records),runs=results),indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps(dict(status='complete',scope='Same complete I/O at 90 MHz, four or fewer local FF copies based on measured >2 ns edges. Extra placement constraints only on added copies; data-state equivalence proved.',copies=len(records),runs=results,wall_seconds=budget.used()),indent=2)+'\n')
if __name__=='__main__':main()
