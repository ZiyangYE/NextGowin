"""Original-pair undefined-aware base check for the exact SDK OREG primitive.

Complements the retained transition-refinement theorem; not full-design SEC.
Defined source INIT and incorrect enable are mandatory negative controls.
"""
import argparse,json,time
from pathlib import Path
from gowin_netlist_format import LIBRARY,EXPECTED
from ownpr_image_storage import sha
from split_primitive_models import units
from yosys_runtime import run,quote


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--negative',choices=('defined_init','wrong_enable'));p.add_argument('--seconds',type=int,default=40)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=False);out=a.out.resolve()
    if sha(LIBRARY)!=EXPECTED:raise ValueError('SDK changed')
    sdk=LIBRARY.read_text(encoding='utf-8');unit=units(sdk)['MULTALU27X18'];sdk=sdk[unit['start']:unit['end']]
    assert sdk.count('wire grstn = GSR.GSRO;')==1
    sdk=sdk.replace('wire grstn = GSR.GSRO;',"wire grstn = 1'b1;")
    pins=".A(a),.B(b),.C(48'b0),.D(26'b0),.SIA(27'b0),.CASI(48'b0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b0),.CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'b0),.RESET(2'b0)"
    initial='initial q=0;' if a.negative=='defined_init' else ''
    gold=f'''module golden(input clk,ce,input [26:0] a,input [17:0] b,output reg [44:0] q);
wire [47:0] product;{initial}
MULTALU27X18 #(.C_SEL(1'b0)) dsp({pins},.CLK(2'b0),.CE(2'b0),.DOUT(product));
always @(posedge clk) if(!ce) q<=product[44:0];
endmodule'''
    enable='ce' if a.negative=='wrong_enable' else '~ce'
    gate=f'''module mapped(input clk,ce,input [26:0] a,input [17:0] b,output [44:0] q);
wire [47:0] product;
MULTALU27X18 #(.C_SEL(1'b0),.OREG_CLK("CLK0")) dsp({pins},.CLK({{1'b0,clk}}),.CE({{1'b0,{enable}}}),.DOUT(product));
assign q=product[44:0];
endmodule'''
    (out/'model.v').write_text(sdk);(out/'gold.v').write_text(gold);(out/'gate.v').write_text(gate)
    commands=[]
    for name,file in [('golden','gold.v'),('mapped','gate.v')]:
        commands+=['read_verilog '+quote(out/'model.v'),'read_verilog '+quote(out/file),f'hierarchy -check -top {name}',
                   'proc','flatten','opt','clk2fflogic','opt','check -assert',f'design -stash {name}']
    commands+=['design -copy-from golden golden','design -copy-from mapped mapped',
               'miter -equiv -ignore_gold_x -make_assert -flatten golden mapped original_pair',
               'hierarchy -check -top original_pair','opt','sat -seq 4 -set-init-undef -set-def-inputs -prove-asserts -verify']
    started=time.time();record={'scope':__doc__,'negative':a.negative,'full_design_equivalence_proved':False,'source_ff_initialization':'defined_zero_negative_control' if initial else 'undefined',
                               'gate_initialization':'SDK unchanged; inactive GSR; no added INIT','sdk_sha256':EXPECTED,'passed':False}
    try:
        record['invocation']=run(commands,out,'proof',timeout=a.seconds)
        record['base_proved']=True;record['negative_detected']=False
    except Exception as error:
        log=(out/'proof.log').read_text(errors='replace') if (out/'proof.log').exists() else ''
        record.update(base_proved=False,negative_detected=bool(a.negative and 'proof did fail' in log.lower()),error=repr(error))
    record['passed']=record['negative_detected'] if a.negative else record['base_proved']
    record['wall_seconds']=time.time()-started;(out/'result.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
    if not record['passed']:raise SystemExit(1)


if __name__=='__main__':main()
