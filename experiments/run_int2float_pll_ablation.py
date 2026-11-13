"""Explicit physical 1:1 PLL experiment; I/O constraints remain active."""
import argparse,copy,json,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from yosys_runtime import run,quote
from gowin_netlist_format import normalize,name_unaliased_bits
from project_timing import audit
from run_routing_ablation import pins
from run_abc_reproduction import sha
from flow_budget import Budget,activate

def insert(vg,out,feedback):
    out.mkdir();run(['read_verilog -lib +/gowin/cells_sim.v','read_verilog -lib +/gowin/cells_xtra_gw5a.v','read_verilog '+quote(vg),'hierarchy -check -top top','blackbox =A:whitebox','write_json '+quote(out/'source.json')],out,'read',60)
    original=json.loads((out/'source.json').read_text());d=copy.deepcopy(original);m=d['modules']['top'];port=m['ports']['clk']['bits'][0]
    buf=[c for c in m['cells'].values() if c['type']=='IBUF' and c['connections']['I']==[port]]
    if len(buf)!=1:raise ValueError('Expected unique clock input buffer')
    old=buf[0]['connections']['O'];n=max(b for v in m['netnames'].values() for b in v['bits'] if isinstance(b,int))
    def wire(name,width=1):
        nonlocal n
        bits=list(range(n+1,n+1+width));n+=width;m['netnames'][name]=dict(hide_name=0,bits=bits,attributes={});return bits
    ref=wire('ng_pll_reference');buf[0]['connections']['O']=ref
    c=dict(type='PLL',hide_name=0,parameters={'FCLKIN':'200.0','IDIV_SEL':f'{4:032b}','FBDIV_SEL':f'{4:032b}','MDIV_SEL':f'{4:032b}','ODIV0_SEL':f'{4:032b}','MDIV_FRAC_SEL':f'{0:032b}','ODIV0_FRAC_SEL':f'{0:032b}','CLKOUT0_EN':'TRUE','CLKFB_SEL':feedback},attributes={},connections={},port_directions={})
    for i in range(1,7):c['parameters']['CLKOUT'+str(i)+'_EN']='FALSE'
    if feedback=='HCLK':c.update(type='CLKDIV',parameters={'DIV_MODE':'1 '})
    for name,p in d['modules'][c['type']]['ports'].items():
        c['port_directions'][name]=p['direction'];w=len(p['bits'])
        if name in ('CLKIN','HCLKIN'):bits=ref
        elif name in ('CLKOUT0','CLKOUT'):bits=old
        elif name=='CLKFB' and feedback=='EXTERNAL':bits=old
        elif p['direction']=='output':bits=wire('ng_pll_unused_'+name,w)
        else:bits=['1' if name.startswith('ENCLK') or name=='RESETN' else '0']*w
        c['connections'][name]=bits
    m['cells']['ng_hclk' if feedback=='HCLK' else 'ng_pll']=c;name_unaliased_bits(d);(out/'with_pll.json').write_text(json.dumps(d)+'\n')
    run(['read_json '+quote(out/'with_pll.json'),'hilomap -hicell VCC V -locell GND G','splitnets -ports','write_verilog -noattr -defparam '+quote(out/'raw.vg')],out,'write',60)
    text,fmt=normalize((out/'raw.vg').read_text());(out/'mapped.vg').write_text(text)
    (out/'clock_change.json').write_text(json.dumps(dict(input_sha256=sha(vg),output_sha256=sha(out/'mapped.vg'),feedback=feedback,input_mhz=200,output_mhz=200,vco_mhz=None if feedback=='HCLK' else 800,scope='Only clock input-buffer fanout redirected through a physical 1:1 clock primitive. Data logic and FF controls unchanged; PLL variants assume lock. HCLK variant uses documented CLKDIV DIV_MODE=1.'),indent=2)+'\n')
    return out/'mapped.vg'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--feedback',choices=['INTERNAL','EXTERNAL','HCLK'],default='INTERNAL');ap.add_argument('--names',nargs='+',default=['vendor','lut4','rom']);ap.add_argument('--all-iob',action='store_true');ap.add_argument('--lut4-source',type=Path);a=ap.parse_args()
    out=a.out.resolve();out.mkdir(exist_ok=False);budget=Budget(1200);activate(budget);manifest=dict(status='running',feedback=a.feedback,scope='200 MHz complete I/O, same pins/electrical settings, physical zero-phase 1:1 PLL. No SDC-only phase adjustment or false paths.',runs=[])
    def save():(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    inputs={'vendor':ROOT/'results/general_synthesis_suite_012/int2float/vendor_synthesis/mapped.vg','lut4':ROOT/'results/compact_output_ablation_001/int2float/05_netlist/mapped.vg','rom':ROOT/'results/int2float_registered_rom_native_001/mapped.vg'}
    if a.lut4_source:inputs['lut4']=a.lut4_source.resolve()
    reference=pins(ROOT/'results/general_synthesis_suite_012/int2float/vendor_pnr/mapped.pin.html');cst=ROOT/'results/routing_ablation_002/int2float/fixed.cst'
    for name in a.names:
        row=dict(name=name,status='running');manifest['runs'].append(row);save()
        try:
            f=out/name;f.mkdir();vg=insert(inputs[name],f/'netlist',a.feedback);sdc=f/'clock.sdc'
            text=(ROOT/'results/int2float_fullio_rom_001/clock200.sdc').read_text()
            pin='ng_hclk/CLKOUT' if a.feedback=='HCLK' else 'ng_pll/CLKOUT0'
            text+=f'create_generated_clock -name sys -source [get_ports {{clk}}] -divide_by 1 [get_pins {{{pin}}}]\n'
            for src in ('clk','sys'):
                for dst in ('clk','sys'):
                    if src==dst=='clk':continue
                    text+=f'set_clock_uncertainty -hold 0.050 -from [get_clocks {{{src}}}] -to [get_clocks {{{dst}}}]\n'
            sdc.write_text(text);pnr=f/'pnr';cmd=[sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(vg),'--sdc',str(sdc),'--cst',str(cst),'--out',str(pnr),'--timeout',str(int(budget.timeout(240,reserve=5))),'--no-iob-registers' if name=='rom' else '--output-iob-registers']
            if a.all_iob:cmd.pop()
            with (f/'runner.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=budget.remaining())
            p=audit(pnr,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':5.,'sys':5.}));(pnr/'physical_audit.json').write_text(json.dumps(p,indent=2)+'\n')
            if pins(pnr/'mapped.pin.html')!=reference:raise ValueError('Actual pins/electrical mismatch')
            row.update(status='complete',frequency=p['frequency_reports'],closed=p['timing_closed'],violations=p['violated_endpoints']);print(name,row,flush=True)
        except Exception as e:row.update(status='failed',error=repr(e));print(name,'FAILED',repr(e),flush=True)
        save()
    manifest.update(status='complete' if all(r['status']=='complete' for r in manifest['runs']) else 'completed_with_failures',wall_seconds=budget.used());save()
if __name__=='__main__':main()
