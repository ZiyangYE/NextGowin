"""Fixed-pin, original-SDC IOB ablations and lean mapping controls."""
import argparse
import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from flow_budget import Budget,activate
from project_timing import audit
from routing_path_diagnosis import diagnose
from run_abc_reproduction import sha
from map_resource_json import map_design
from nextgowin import materialize
from registered_benchmark import prove
from gowin_blif import read_blif


def pins(path):
    text=path.read_text();text=text.split('<a name="Pinout_by_Port_Name">',1)[1].split('</table>',1)[0]
    rows=[]
    for row in re.findall(r'<tr>(.*?)</tr>',text,re.S):
        fields=[html.unescape(re.sub('<[^>]+>','',v)).strip() for v in re.findall(r'<td[^>]*>(.*?)</td>',row,re.S)]
        if len(fields)==19 and fields[4] in ('in','out','inout'):rows.append(fields)
    if not rows:raise ValueError('No pin assignment records')
    result={}
    for r in rows:
        name=r[0].removeprefix('\\')
        if name in result:raise ValueError('Ambiguous normalized port name')
        result[name]=dict(pin=r[2].split('/')[0],io_type=r[7],drive=r[8],pull=r[9])
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--reuse-from',type=Path);ap.add_argument('--budget',type=int,default=3600);a=ap.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False);budget=Budget(a.budget);activate(budget)
    report=dict(status='running',predeclared_io_designs=['ctrl','int2float','router'],internal_design='sin',runs=[],
        scope='Controlled implementation and synthesis ablations. New fixed CST; original 100 MHz SDC retained, separate 160 MHz target on both sides. No board programming.')
    def save():(out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    save()
    def run_case(name,variant,vg,sdc,cst,core,pin_reference):
        folder=out/name/variant;folder.parent.mkdir(exist_ok=True);row=dict(design=name,variant=variant,status='running',netlist_sha256=sha(vg),sdc_sha256=sha(sdc),cst_sha256=sha(cst));report['runs'].append(row);save()
        try:
            limit=int(budget.timeout(300,reserve=10));cmd=[sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(vg),'--sdc',str(sdc),'--cst',str(cst),'--out',str(folder),'--timeout',str(limit)]
            if core:cmd.append('--no-iob-registers')
            prior=a.reuse_from/name/variant if a.reuse_from else None
            if prior and (prior/'manifest.json').exists():
                old=json.loads((prior/'manifest.json').read_text())
                for key in ('netlist_sha256','sdc_sha256','cst_sha256'):
                    if old.get('netlist_sha256_before' if key=='netlist_sha256' else key)!=row[key]:raise ValueError('Reused input hash mismatch: '+key)
                if old.get('returncode')!=0:raise ValueError('Preserved previous native failure: '+str(prior))
                shutil.copytree(prior,folder);row['reused_from']=str(prior)
            else:
                with (folder.parent/(variant+'.log')).open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=budget.remaining())
            period=6.25 if '160' in variant else 10.
            physical=audit(folder,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':period}))
            observed=pins(folder/'mapped.pin.html')
            if observed!=pin_reference:raise ValueError('Actual pins or electrical settings differ from fixed reference')
            paths=diagnose((folder/'mapped.tr').read_text());(folder/'routing_diagnosis.json').write_text(json.dumps(paths,indent=2)+'\n')
            row.update(status='complete',fmax_mhz=physical['frequency_reports']['clk']['reported_fmax_mhz'],violations=physical['violated_endpoints'],timing_closed=physical['timing_closed'],resources=physical['implementation'],worst_path=paths[0],pins_verified=True)
            print(name,variant,row['fmax_mhz'],row['violations'],'wire',round(paths[0]['data_wire_ns'],3),'clock',round(paths[0]['launch_clock_delay_ns'],3),flush=True)
        except Exception as error:row.update(status='failed',error=repr(error));print(name,variant,'FAILED',repr(error),flush=True)
        save()
    for name in ['ctrl','int2float','router','sin']:
        p=out/name;p.mkdir();base=ROOT/'results/general_synthesis_suite_012'/name;current=ROOT/'results/fanout_development_suite_001'/name
        reference=pins(base/'vendor_pnr/mapped.pin.html');cst=p/'fixed.cst';lines=[]
        for port,r in reference.items():
            if not re.fullmatch(r'[\w]+(?:\[\d+\])?',port) or not re.fullmatch(r'[A-Z]+\d+',r['pin']):raise ValueError('Unexpected pin syntax')
            lines.append(f'IO_LOC "{port}" {r["pin"]};')
            attrs=f'IO_TYPE={r["io_type"]} PULL_MODE={r["pull"]}'
            if r['drive'].isdigit():attrs+=' DRIVE='+r['drive']
            lines.append(f'IO_PORT "{port}" {attrs};')
        cst.write_text('\n'.join(lines)+'\n');sdc=p/'clock.sdc';shutil.copy2(current/'clock.sdc',sdc)
        inputs={'vendor':base/'vendor_synthesis/mapped.vg','own':current/'own/05_netlist/mapped.vg'}
        for side,vg in inputs.items():run_case(name,side+'_core100',vg,sdc,cst,True,reference)
        if name!='sin':
            for side,vg in inputs.items():run_case(name,side+'_iob100',vg,sdc,cst,False,reference)
            fast=p/'clock160.sdc';text=sdc.read_text()
            if text.count('-period 10 ')!=1:raise ValueError('Unexpected baseline clock')
            fast.write_text(text.replace('-period 10 ','-period 6.25 '))
            for side,vg in inputs.items():run_case(name,side+'_iob160',vg,fast,cst,False,reference)
        else:
            for recipe in ('wide','lut4'):
                f=p/(recipe+'_lean');f.mkdir();resource=f/'03_resources';resource.mkdir()
                for file in ('register_choices.json','dsp_cells.v'):
                    if (current/'own/03_resources'/file).exists():shutil.copy2(current/'own/03_resources'/file,resource/file)
                map_design(current/'own/03_resources/resources.json',f/'04_mapping',10000,'arch_delay',recipe)
                materialize(f/'04_mapping/mapped.json',f/'05_netlist','top',current/'own/02_bridge/vendor_cells.v',optimize_resources=True,register_alternatives=True,register_control_penalty_ps=206)
                proof=f/'functional_proof';proof.mkdir();shutil.copy2(current/'reference.blif',proof/'reference.blif')
                prove(json.loads((f/'05_netlist/final.json').read_text()),read_blif(current/'reference.blif',max_inputs=11),proof)
                run_case(name,recipe+'_lean_core100',f/'05_netlist/mapped.vg',sdc,cst,True,reference)
    report.update(status='complete' if all(r['status']=='complete' for r in report['runs']) else 'completed_with_failures',wall_seconds=budget.used());save()


if __name__=='__main__':main()
