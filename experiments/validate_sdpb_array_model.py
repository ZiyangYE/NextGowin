"""Compare the array proof adapter with verbatim installed SDK simulation."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gw5a_sdpb_array_model import model,address_certificate
from gowin_netlist_format import LIBRARY,EXPECTED
from ownpr_image_storage import sha
from split_primitive_models import units
from workspace_budget import reserve
SUITE=ROOT/'build/dependencies/oss-cad-suite'


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('out',type=Path)
    parser.add_argument('--mutation',choices=('address','clock','init','read_mode','reset_mode','byte_enable'))
    args=parser.parse_args();out=args.out.resolve();reserve(ROOT,20_000_000);out.mkdir(exist_ok=False)
    if sha(LIBRARY)!=EXPECTED:raise ValueError('SDK changed')
    text=LIBRARY.read_text(encoding='utf-8');index=units(text)
    body=lambda n:text[index[n]['start']:index[n]['end']]
    sdk=body('SDPB').replace('module SDPB ', 'module SDPB_sdk ',1)
    adapter=model('SDPB_array')
    changes=dict(address=('ra=ADB[13:SHIFT]','ra=ADB[13:SHIFT] ^ 1\'b1'),clock=('posedge CLKA','posedge CLKB'),
        init=('INIT_BITS[address*W+:W]','(INIT_BITS[address*W+:W] ^ 1\'b1)'),
        read_mode=('assign DO=READ_MODE==0?bypass:pipeline;','assign DO=READ_MODE==1?bypass:pipeline;'),
        reset_mode=('if(RESET_MODE=="ASYNC")','if(RESET_MODE=="SYNC")'),byte_enable=('ADA[lane/8]','ADA[(lane/8)^1]'))
    if args.mutation:
        old,new=changes[args.mutation]
        if adapter.count(old)!=1:raise ValueError('Mutation pattern changed')
        adapter=adapter.replace(old,new)
    (out/'models.v').write_text('`timescale 1ns/1ps\n'+body('GSR')+'\n'+sdk+'\n'+adapter)
    tb=['`timescale 1ns/1ps','module tb; reg global_reset=1; GSR GSR(.GSRI(global_reset)); reg clk_a=0,clk_b=0; reg [31:0] data=0; reg [13:0] wa=0,ra=0; reg cea=0,ceb=0,oce=0,rst=0; reg [2:0] ba=0,bb=0; integer step;']
    specs=[]
    for width in (1,2,4,8,16,32):
        for mode in (0,1):
            for reset in ('SYNC','ASYNC'):
                n=f'w{width}_m{mode}_{reset}';specs.append(n)
                tb.append(f'wire [31:0] sdk_{n},array_{n};')
                for kind,prefix in [('SDPB_sdk','sdk'),('SDPB_array','array')]:
                    tb.append(f'{kind} #(.BIT_WIDTH_0({width}),.BIT_WIDTH_1({width}),.READ_MODE({mode}),.RESET_MODE("{reset}")) {prefix}_{n}_inst(.DO({prefix}_{n}),.DI(data),.ADA(wa),.ADB(ra),.CLKA(clk_a),.CLKB(clk_b),.CEA(cea),.CEB(ceb),.OCE(oce),.RESET(rst),.BLKSELA(ba),.BLKSELB(bb));')
    checks='\n'.join(f'if(sdk_{n} !== array_{n}) begin $display("MISMATCH {n} step=%0d sdk=%h adapter=%h",step,sdk_{n},array_{n}); $fatal(1); end' for n in specs)
    tb.append('initial begin #2; for(step=0;step<4096;step=step+1) begin\n'
        ' data=(step*32\'h9e3779b9)^32\'h12345678; wa=((step%16)<<5)|(step%32); ra=(((step/3)%16)<<5)|(step%32);\n'
        ' cea=(step%7)!=0; ceb=(step%5)!=0; oce=(step%3)!=0; rst=(step%97)==0; ba=((step%11)==0)?1:0; bb=((step%13)==0)?1:0;\n'
        '#2;\n'+checks+'\nclk_a=step%2; clk_b=(step/2)%2; #2;\n'+checks+'\nend\n$display("SDK_ARRAY_ALL_CASES_MATCHED");$finish;end\nendmodule')
    (out/'tb.v').write_text('\n'.join(tb));env=dict(os.environ)
    env['PATH']=str(SUITE/'lib')+os.pathsep+str(SUITE/'bin')+os.pathsep+env.get('PATH','')
    result=dict(status='running',sdk_sha256=EXPECTED,model_sha256=sha(out/'models.v'),testbench_sha256=sha(out/'tb.v'),
        address_certificate=address_certificate(),configurations=len(specs),stimulus_steps=4096,whole_design_equivalence_proved=False,negative_control=args.mutation,
        compiler_sha256=sha(SUITE/'bin/iverilog.exe'),simulator_sha256=sha(SUITE/'bin/vvp.exe'))
    def save():(out/'manifest.json').write_text(json.dumps(result,indent=2))
    save();started=time.time()
    try:
        for name,command,cap in [('compile',[str(SUITE/'bin/iverilog.exe'),'-B'+str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',str(out/'simulation.vvp'),str(out/'models.v'),str(out/'tb.v')],30),('simulate',[str(SUITE/'bin/vvp.exe'),'-M-','-M'+str(SUITE/'lib/ivl'),str(out/'simulation.vvp')],90)]:
            with (out/(name+'.log')).open('x') as log:cp=subprocess.run(command,cwd=out,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=cap)
            result[name+'_returncode']=cp.returncode
            if name=='simulate' and args.mutation:
                if not cp.returncode or 'MISMATCH' not in (out/'simulate.log').read_text():raise ValueError('Negative mutation was not observably rejected')
                result.update(status='negative_control_rejected',elapsed_seconds=time.time()-started);save();print(json.dumps(result));return
            if cp.returncode:raise ValueError(name+' failed')
        if 'SDK_ARRAY_ALL_CASES_MATCHED' not in (out/'simulate.log').read_text():raise ValueError('Missing simulation completion marker')
        result.update(status='sdk_array_differential_simulation_passed',elapsed_seconds=time.time()-started,
            scope='All supported equal widths, read modes and reset modes; deterministic independent/coincident clock, CE, BLKSEL, byte-enable and reset stimulus. Exhaustive address/lane identity. Simulation is not an unbounded equivalence proof.')
    except Exception as error:result.update(status='adapter_validation_failed',error=repr(error));save();raise
    save();print(json.dumps(result,indent=2))


if __name__=='__main__':main()
