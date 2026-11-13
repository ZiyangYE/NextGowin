"""Predeclared multi-design, same-RTL native-versus-own physical comparison.

The vendor flow is a baseline only. No vendor synthesized logic is linked into
the own candidate. Every registered core must pass full readback CEC and exact
launch/capture control/init checks before its physical result is accepted.
"""
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from gowin_probe import ROOT,PART
from gowin_blif import read_blif
from registered_benchmark import rtl,prove
from native_synthesis import synthesize
from yosys_runtime import run,quote
from project_timing import parse,implementation
from run_abc_reproduction import sha


DEVELOPMENT=['ctrl','priority','int2float','cavlc','dec','router','i2c','sin','log2']
VALIDATION=['bar','multiplier']
DESIGNS=DEVELOPMENT+VALIDATION


def method_hashes():
    paths=list((ROOT/'scripts').glob('*.py'))+[ROOT/'configs/gw5a_arch_cost_v2.json',ROOT/'results/timing/gowin138_model.json']
    return {p.relative_to(ROOT).as_posix():sha(p) for p in sorted(paths)}


def physical(vg,sdc,out,timeout=300):
    with (out.parent/(out.name+'_runner.log')).open('w') as log:
        subprocess.run([sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(vg),'--sdc',str(sdc),'--out',str(out),
                        '--no-iob-registers','--timeout',str(timeout)],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout+30)
    profile=dict(part=PART,clock_periods_ns={'clk':10.0})
    timing=parse((out/'mapped.tr').read_text(errors='replace'),sdc.read_text(),profile)
    resources=implementation((out/'mapped.rpt.txt').read_text(errors='replace'),profile)
    return dict(fmax_mhz=timing['frequency_reports']['clk']['reported_fmax_mhz'],violated_endpoints=timing['violated_endpoints'],timing_closed=timing['timing_closed'],
                timing=timing,resources=resources,netlist_sha256=sha(vg),sdc_sha256=sha(sdc),native=json.loads((out/'manifest.json').read_text()))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--resume',action='store_true');ap.add_argument('--only',choices=DESIGNS)
    ap.add_argument('--pnr-timeout',type=int,default=300);args=ap.parse_args()
    if not 0<args.pnr_timeout<=3600:ap.error('P&R timeout must be within one hour')
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=args.resume)
    result=dict(schema='general-synthesis-comparison-v2',status='running',designs_predeclared=DESIGNS,
                partition=dict(development=DEVELOPMENT,held_out=VALIDATION),method_sha256=method_hashes(),pnr_timeout=args.pnr_timeout,runs={},
                policy='Same RTL, SDK/part, 100 MHz SDC with 50 ps hold uncertainty, P&R options and core FF policy. All declared designs included; failures are not wins.',
                own_method='Resource inference then architecture-costed delay-first cuts and exact cofactor choices; no vendor synthesis substitution')
    manifest=out/'manifest.json'
    if args.resume and manifest.exists():result=json.loads(manifest.read_text())
    if result.get('pnr_timeout')!=args.pnr_timeout:raise ValueError('P&R budget changed; create a new output directory')
    if result.get('method_sha256')!=method_hashes():raise ValueError('Method changed since this cohort was frozen; create a new output directory')
    def save():manifest.write_text(json.dumps(result,indent=2)+'\n')
    save()
    for name in DESIGNS:
        if result['method_sha256']!=method_hashes():raise ValueError('Method changed during this cohort; results cannot be pooled')
        if args.only and name!=args.only:continue
        if result['runs'].get(name,{}).get('status')=='complete':continue
        p=out/name;p.mkdir(exist_ok=args.resume)
        original=ROOT/'third_party/epfl'/('arithmetic' if name in ('sin','log2','bar','multiplier') else 'random_control')/(name+'.blif');network=read_blif(original,max_inputs=11)
        source=p/'rtl.v';source.write_text(rtl(network));(p/'reference.blif').write_bytes(original.read_bytes())
        sdc=p/'clock.sdc';sdc.write_text('create_clock -name clk -period 10 [get_ports {clk}]\n'
            'set_input_delay -max 0 -clock clk [get_ports {a[*]}]\nset_input_delay -min 0 -clock clk [get_ports {a[*]}]\n'
            'set_output_delay -max 0 -clock clk [get_ports {q[*]}]\nset_output_delay -min 0 -clock clk [get_ports {q[*]}]\n'
            'set_clock_uncertainty -hold 0.050 -from [get_clocks {clk}] -to [get_clocks {clk}]\n')
        row=dict(status='running',benchmark_sha256=sha(original),rtl_sha256=sha(source),sdc_sha256=sha(sdc),inputs=len(network['inputs']),outputs=len(network['outputs']))
        result['runs'][name]=row;save();print('Starting',name,flush=True)
        try:
            native_folder=p/'vendor_synthesis'
            if not native_folder.exists():vg,synth=synthesize(source,native_folder,sdc=sdc)
            else:
                synth=json.loads((native_folder/'manifest.json').read_text());vg=native_folder/'mapped.vg'
                if synth['status']!='complete' or synth['source_sha256']!=sha(source):raise ValueError('Invalid vendor synthesis checkpoint')
            native_json=p/'vendor_json';native_json.mkdir(exist_ok=args.resume)
            valid_import=(native_json/'read.invocation.json').exists() and json.loads((native_json/'read.invocation.json').read_text())['returncode']==0
            if not valid_import:
                if (native_json/'read.console.log').exists():
                    (native_json/'read_failed.log').write_bytes((native_json/'read.console.log').read_bytes())
                run(['read_verilog -lib +/gowin/cells_sim.v','read_verilog -lib +/gowin/cells_xtra_gw5a.v',
                     'read_verilog '+quote(vg),'blackbox =A:whitebox','hierarchy -check -top top','write_json '+quote(native_json/'design.json')],native_json,'read')
            vp=p/'vendor_proof';vp.mkdir(exist_ok=args.resume);(vp/'reference.blif').write_bytes(original.read_bytes())
            row['vendor_equivalence']=prove(json.loads((native_json/'design.json').read_text()),network,vp)
            if not (p/'vendor_pnr').exists():row['vendor']=physical(vg,sdc,p/'vendor_pnr',args.pnr_timeout)
            else:
                timing=parse((p/'vendor_pnr/mapped.tr').read_text(),sdc.read_text(),dict(part=PART,clock_periods_ns={'clk':10.0}))
                row['vendor']=dict(fmax_mhz=timing['frequency_reports']['clk']['reported_fmax_mhz'],timing_closed=timing['timing_closed'],violated_endpoints=timing['violated_endpoints'],timing=timing)
            project=p/'frontend.prj'
            tree=ET.Element('Project');ET.SubElement(tree,'Device',dict(partNumber=PART));fl=ET.SubElement(tree,'FileList')
            ET.SubElement(fl,'File',dict(path=str(source),type='verilog'));opts=ET.SubElement(tree,'OptionList');ET.SubElement(opts,'Option',dict(type='top_module',value='top'))
            ET.ElementTree(tree).write(project,encoding='utf-8',xml_declaration=True)
            config=dict(project=str(project),sdc=str(sdc),period_ps=10000,strategy='arch_delay',resource_optimizations=True,register_alternatives=True,no_iob_registers=True,timeout=300)
            (p/'config.json').write_text(json.dumps(config,indent=2)+'\n')
            if not (p/'own').exists():
                with (p/'own.log').open('w') as log:
                    subprocess.run([sys.executable,str(ROOT/'scripts/nextgowin.py'),'--config',str(p/'config.json'),'--out',str(p/'own'),'--stop-after','mapping'],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=360)
            op=p/'own_proof';op.mkdir(exist_ok=args.resume);(op/'reference.blif').write_bytes(original.read_bytes())
            row['own_equivalence']=prove(json.loads((p/'own/05_netlist/final.json').read_text()),network,op)
            row['own']=physical(p/'own/05_netlist/mapped.vg',sdc,p/'own_pnr',args.pnr_timeout)
            if result['method_sha256']!=method_hashes():raise ValueError('Method changed while processing design')
            row.update(status='complete',fmax_ratio=row['own']['fmax_mhz']/row['vendor']['fmax_mhz'])
            print(name,'vendor',row['vendor']['fmax_mhz'],'own',row['own']['fmax_mhz'],flush=True)
        except Exception as e:
            row.update(status='failed',error=repr(e));print(name,'FAILED',repr(e),flush=True)
        save()
    complete=[r for r in result['runs'].values() if r['status']=='complete']
    all_done=len(complete)==len(DESIGNS)
    result['status']='complete' if all_done else 'incomplete_or_failed'
    result['summary']=dict(declared=len(DESIGNS),completed=len(complete),geomean_fmax_ratio=math.exp(sum(math.log(r['fmax_ratio']) for r in complete)/len(complete)) if complete else None,
                           improvements=sum(r['fmax_ratio']>1 for r in complete),
                           timing_valid_improvements=sum(r['fmax_ratio']>1 and r['own']['timing_closed'] and r['vendor']['timing_closed'] for r in complete),superiority_established=False,
                           note='Single-run screening only; repeats, held-out results and full-project validation are required before a general superiority claim.')
    save();print(json.dumps(result['summary']))


if __name__=='__main__':main()
