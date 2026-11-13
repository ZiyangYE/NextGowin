"""Differential SDK simulation; deliberately not an equivalence certificate."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import time

from ownpr_sdpb_formal import sdk_body, word_model
from yosys_runtime import SUITE


def bench(quick=False):
    rng=random.Random(230013)
    params=',\n'.join(f".INIT_RAM_{i:02X}(256'h{rng.getrandbits(256):064x})" for i in range(64))
    instances=[]
    for w in (1,2,4,8,16,32):
        for mode in (0,1):
            tag=f'w{w}m{mode}'
            instances.append(f'''wire [31:0] sdk_{tag}, word_{tag};
SDPB #(.BIT_WIDTH_0({w}),.BIT_WIDTH_1({w}),.READ_MODE({mode}),
 .BLK_SEL_0(3'b101),.BLK_SEL_1(3'b011),{params})
 ref_{tag}(sdk_{tag},DI,BA,BB,ADA,ADB,CA,CB,CEA,CEB,OCE,RST);
WORD_SDPB #(.BIT_WIDTH_0({w}),.BIT_WIDTH_1({w}),.READ_MODE({mode}),
 .BLK_SEL_0(3'b101),.BLK_SEL_1(3'b011),{params})
 dut_{tag}(word_{tag},DI,BA,BB,ADA,ADB,CA,CB,CEA,CEB,OCE,RST);
always @(negedge check) begin
 if (sdk_{tag} !== word_{tag}) begin
  $display("FAIL {tag} step=%d addrA=%d addrB=%d sdk=%h word=%h",step,ADA,ADB,sdk_{tag},word_{tag});
  $fatal(1);
 end
end
''')
    result='''`timescale 1ns/1ps
module tb;
reg [31:0] DI;
reg [13:0] ADA,ADB;
reg [2:0] BA,BB;
reg CA,CB,CEA,CEB,OCE,RST,check;
integer step,i,k,seed;
task tick;
input a,b;
begin
 #2; CA=a; CB=b; #2; check=1; #1;check=0; #1;CA=0;CB=0; step=step+1;
end endtask
'''+''.join(instances)+'''
initial begin
 seed=230013;step=0;CA=0;CB=0;CEA=0;CEB=1;OCE=1;RST=0;
 BA=5;BB=3;ADA=0;ADB=0;DI=0;check=0;
 // Read every initial bit address, including ignored low address bits.
 for(i=0;i<16384;i=i+1) begin ADB=i;tick(0,1);end
 tick(0,1);
 // Write every address. For wide ports this enumerates every byte mask.
 CEA=1;
 for(i=0;i<16384;i=i+1) begin
   ADA=i;ADB=i;DI=$random(seed);tick(1,1);tick(0,1);
 end
 // Verify retained memory independently of the write sequence.
 CEA=0;
 for(i=0;i<16384;i=i+1) begin ADB=i;tick(0,1);end
 tick(0,1);
 // Deterministically cover every CE/OCE/reset/block-match/edge combination.
 for(i=0;i<256;i=i+1) begin
   CEA=i[0];CEB=i[1];OCE=i[2];RST=i[3];
   BA=i[4]?5:2;BB=i[5]?3:4;
   ADA=16383-i;ADB=ADA;DI=$random(seed);tick(i[6],i[7]);
 end
 // Independent and coincident clocks, collisions, block mismatches, resets.
 for(i=0;i<12000;i=i+1) begin
   k=$random(seed);CEA=k[0];CEB=k[1];OCE=k[2];RST=k[3:0]==0;
   BA=k[4]?5:2;BB=k[5]?3:4;
   ADA=$random(seed);ADB=k[6]?ADA:$random(seed);DI=$random(seed);
   tick(k[7],k[8]);
 end
 $display("PASS SDK_DIFFERENTIAL profiles=12 steps=%d",step);$finish;
end
endmodule
'''
    if quick:
        result=result.replace('i<16384','i<64').replace('i<12000','i<256')
    return result


MUTATIONS={
    'init_bit':('words[i]=IMAGE[i*W +: W];','words[i]=IMAGE[i*W +: W] ^ (i==0);'),
    'byte_mask':('&& ADA[lane])','&& ADA[(lane+1)%(W/8)])'),
    'block_select':('BLKSELB==BLK_SEL_1','BLKSELB!=BLK_SEL_1'),
    'high_output':("(32-W){1'b0}","(32-W){1'b1}"),
    'reset_priority':('if(RESET) begin','if(RESET && !OCE) begin'),
    'write_first':('bp_reg<=words[ra];',
                  'bp_reg<=(CEA && BLKSELA==BLK_SEL_0 && wa==ra) ? DI[W-1:0] : words[ra];'),
}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--timeout',type=int,default=120)
    ap.add_argument('--mutation',choices=tuple(MUTATIONS))
    ap.add_argument('--quick',action='store_true');a=ap.parse_args()
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    original=sdk_body()
    needle='wire grstn = GSR.GSRO;'
    if original.count(needle)!=1:raise ValueError('GSR reference changed')
    word=word_model()
    if a.mutation:
        before,after=MUTATIONS[a.mutation]
        if word.count(before)!=1:raise ValueError('Mutation anchor changed')
        word=word.replace(before,after)
    files={'sdk.v':original.replace(needle,"wire grstn = 1'b1;"),
           'word.v':word.replace('module SDPB (','module WORD_SDPB ('),
           'tb.v':bench(a.quick)}
    for name,body in files.items():(out/name).write_text(body,encoding='utf-8')
    env=dict(os.environ);env['PATH']=str(SUITE/'lib')+os.pathsep+str(SUITE/'bin')+os.pathsep+env.get('PATH','')
    if os.name=='nt':ctypes.windll.kernel32.SetErrorMode(0x8003)
    begin=time.time();runs=[]
    for name,cmd in [('compile',[str(SUITE/'bin/iverilog.exe'),'-B',str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',str(out/'sim.vvp'),*[str(out/f) for f in files]]),
                     ('simulate',[str(SUITE/'bin/vvp.exe'),'-M','-','-M',str(SUITE/'lib/ivl'),str(out/'sim.vvp')])]:
        try:
            with (out/(name+'.log')).open('w',encoding='utf-8') as log:
                cp=subprocess.run(cmd,cwd=out,env=env,stdout=log,stderr=subprocess.STDOUT,
                                  timeout=max(1,a.timeout-(time.time()-begin)),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            runs.append({'stage':name,'returncode':cp.returncode})
            if cp.returncode:break
        except subprocess.TimeoutExpired:
            runs.append({'stage':name,'returncode':None,'timeout':True});break
    report={'scope':'Differential simulation only; NOT an unbounded proof or production admission',
            'contract':'constant inactive global GSR; defined inputs; stable inputs before clock events; SYNC reset; symmetric widths',
            'profiles':12,'runs':runs,'wall_seconds':time.time()-begin,
            'source_sha256':{name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in files}}
    report.update(mutation=a.mutation,quick=a.quick)
    sim=(out/'simulate.log').read_text() if (out/'simulate.log').exists() else ''
    report['adapter_matches_sdk']=len(runs)==2 and all(r['returncode']==0 for r in runs) and 'PASS SDK_DIFFERENTIAL' in sim
    report['negative_control_detected']=bool(a.mutation and len(runs)==2 and runs[0]['returncode']==0 and runs[1]['returncode'] not in (None,0) and 'FAIL w' in sim)
    report['passed']=report['negative_control_detected'] if a.mutation else report['adapter_matches_sdk']
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
