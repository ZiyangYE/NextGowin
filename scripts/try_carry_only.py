"""Build a CEC-checked experimental soft-comparison candidate, without P&R."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from lower_carry_only import lower
from gowin_netlist_format import LIBRARY,EXPECTED
from yosys_runtime import run,quote,SUITE
from run_abc_reproduction import ROOT,sha
from map_resource_json import map_design
from nextgowin import materialize


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True);ap.add_argument('--top',required=True)
    ap.add_argument('--vendor-library',type=Path,required=True);args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    if sha(LIBRARY)!=EXPECTED:raise ValueError('Native model changed')
    tb='''module tb;reg a,b,s,c;wire [2:0] actual;integer i;
ALU #(.ALU_MODE(0)) x0(.I0(a),.I1(b),.I3(s),.CIN(c),.COUT(actual[0]));
ALU #(.ALU_MODE(1)) x1(.I0(a),.I1(b),.I3(s),.CIN(c),.COUT(actual[1]));
ALU #(.ALU_MODE(2)) x2(.I0(a),.I1(b),.I3(s),.CIN(c),.COUT(actual[2]));
initial begin for(i=0;i<16;i=i+1)begin {a,b,s,c}=i;#1;
if(actual[0]!==((a^b)?c:a) || actual[1]!==((a^~b)?c:a) ||
actual[2]!==((a^b^~s)?c:a))$fatal(1,"native carry mismatch");
end $display("PASS all native carry truth tables");$finish;end endmodule'''
    (out/'tb.v').write_text(tb)
    env=dict(os.environ);env['PATH']=str(SUITE/'lib')+os.pathsep+env.get('PATH','')
    commands=[[str(SUITE/'bin/iverilog.exe'),'-B',str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',str(out/'sim'),str(out/'tb.v'),str(LIBRARY)],
              [str(SUITE/'bin/vvp.exe'),'-M-','-M'+str(SUITE/'lib/ivl'),str(out/'sim')]]
    for i,command in enumerate(commands):
        cp=subprocess.run(command,env=env,capture_output=True,text=True,timeout=30)
        (out/f'truth_{i}.log').write_text(cp.stdout+cp.stderr)
        if cp.returncode:raise RuntimeError('Native carry truth check failed')
    if 'PASS all native carry truth tables' not in cp.stdout:raise RuntimeError('Truth check did not finish')
    design,record=lower(json.loads(args.input.read_text()))
    record.update(input_sha256=sha(args.input),implementation_sha256=sha(ROOT/'scripts/lower_carry_only.py'),native_model_sha256=EXPECTED)
    record['status']='building_candidate'
    (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    resources=out/'03_resources';resources.mkdir()
    (resources/'lowered.json').write_text(json.dumps(design)+'\n')
    dsp=args.input.parent/'dsp_cells.v'
    if dsp.exists():(resources/'dsp_cells.v').write_bytes(dsp.read_bytes())
    record['optimization']=run(['read_json '+quote(resources/'lowered.json'),'opt -full','techmap','opt -fast','check -assert -noinit',
                                'write_json '+quote(resources/'resources.json')],resources,'opt')
    record['mapping']=map_design(resources/'resources.json',out/'04_mapping',12500,'acd_remap')
    aliases={'core_selected_Z':dict(module='thefly_top',net='host.core_selected')} if args.top=='thefly_top' else {}
    record['emission']=materialize(out/'04_mapping/mapped.json',out/'05_netlist',args.top,args.vendor_library,net_aliases=aliases)
    record['status']='candidate_only';record['scope']='Local native truth check and Boolean cut CEC; no P&R, no full RTL sequential proof.'
    (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(dict(status=record['status'],alu_cells_removed=record['alu_cells_removed'])))


if __name__=='__main__':main()
