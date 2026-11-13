"""Small symbolic obligations for the SDK/word-array state relation.

This checks extracted storage/read-address and output-register transitions for
every bit address, not a bounded address sample. It is NOT full RTL SEC: extraction
and the complete inductive composition remain separate review obligations.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time

from ownpr_sdpb_formal import sdk_body,word_model
from yosys_runtime import run,quote


def kernel(w):
    source=sdk_body();model=word_model();shift=w.bit_length()-1
    if not re.search(r'assign pcea\s*=\s*CEA && bs_ena && grstn;',source):
        raise ValueError('SDK write enable changed')
    if not re.search(r'assign pceb\s*=\s*CEB && bs_enb;',source):
        raise ValueError('SDK read enable changed')
    start=source.index('always @(posedge CLKA) begin')
    end=source.index('always @ (bp_reg_async',start)
    sections=re.split(r'(?:else\s+)?if\(bit_width_d0==(\d+)\)',source[start:end])
    sections={int(sections[i]):sections[i+1] for i in range(1,len(sections),2)}
    pattern=r"(?:if\(ADA\[(\d+)\]\s*==\s*1'b1\)\s*)?(\{[^{}]+\}|ram_MEM\[addr_a\])\s*<=\s*DI\[([^\]]+)\];"
    mapping=[]
    for match in re.finditer(pattern,sections[w]):
        guard=None if match[1] is None else int(match[1])
        lhs=match[2].strip('{}').split(',');offsets=[]
        for term in lhs:
            n=re.fullmatch(r'\s*ram_MEM\[addr_a(?:\+(\d+))?\]\s*',term)
            if not n:raise ValueError('SDK write grammar changed')
            offsets.append(int(n[1] or 0))
        rhs=match[3].replace('BIT_WIDTH_0-1',str(w-1)).split(':')
        hi=int(rhs[0]);lo=int(rhs[-1]);data=list(range(hi,lo-1,-1))
        if len(data)!=len(offsets):raise ValueError('Write bit inventory mismatch')
        mapping.extend({'offset':o,'data_bit':d,'byte_guard':guard} for o,d in zip(offsets,data))
    if sorted(x['offset'] for x in mapping)!=list(range(w)):raise ValueError('Incomplete/overlapping SDK write coverage')
    if any(x['byte_guard']!=(x['offset']//8 if w>=16 else None) for x in mapping):raise ValueError('Unexpected SDK byte guards')
    align=f"{{ADA[13:{shift}],{shift}'b{'0'*shift}}}"
    align_b=f"{{ADB[13:{shift}],{shift}'b{'0'*shift}}}"
    if f'addr_a[13:0] = {align};' not in source or f'addr_b[13:0] = {align_b};' not in source:
        raise ValueError('SDK address alignment changed')
    read=re.search(rf'mem_b\[{w-1}:0\]\s*=\s*\{{([^{{}}]+)\}};',source)
    if not read:raise ValueError('SDK read inventory missing')
    read_offsets=[]
    for term in read[1].split(','):
        m=re.fullmatch(r'\s*ram_MEM\[addr_b(?:\+(\d+))?\]\s*',term)
        if not m:raise ValueError('SDK read grammar changed')
        read_offsets.append(int(m[1] or 0))
    if read_offsets!=list(reversed(range(w))):raise ValueError('SDK read bit ordering changed')
    # Bind the compact-side formulas to the implementation template. Unsupported
    # template changes reject this extraction instead of silently checking stale logic.
    for needle in ['wire [13:0] wa=ADA>>SHIFT;','wire [13:0] ra=ADB>>SHIFT;',
                   'if(CEA && BLKSELA==BLK_SEL_0) words[wa]<=DI[W-1:0];',
                   'if(CEA && BLKSELA==BLK_SEL_0 && ADA[lane])',
                   'words[wa][lane*8 +: 8]<=DI[lane*8 +: 8];']:
        if model.count(needle)!=1:raise ValueError('Word storage template changed')
    sdk_next='old_bit'
    for item in mapping:
        guard='' if item['byte_guard'] is None else f" && ADA[{item['byte_guard']}]"
        sdk_next=f"((CEA && BA==SEL_A{guard}) && K==sdk_a+14'd{item['offset']}) ? DI[{item['data_bit']}] : ({sdk_next})"
    sdk_sync=source[source.index('always @(posedge CLKB or negedge grstn) begin'):]
    sdk_sync=sdk_sync[:sdk_sync.index('endmodule')].strip()
    sdk_sync=sdk_sync.replace('always @(posedge CLKB or negedge grstn) begin',
        'always @* begin\n sdk_bp_next=old_bp; sdk_pl_next=old_pl;')
    sdk_sync=sdk_sync.replace('pl_reg_sync','sdk_pl_next').replace('bp_reg_sync','sdk_bp_next')
    sdk_sync=sdk_sync.replace('bp_reg','old_bp').replace('mem_b','read_value').replace('<=','=')
    sdk_sync=sdk_sync.replace('BIT_WIDTH_1',str(w)).replace('grstn',"1'b1").replace('pceb','(CEB && BB==SEL_B)')
    model_sync=model[model.index('    always @(posedge CLKB) begin'):model.index('    assign DO=')]
    model_sync=model_sync.replace('always @(posedge CLKB) begin',
        'always @* begin\n word_bp_next=bp; word_pl_next=pl;')
    model_sync=model_sync.replace('bp_reg<=','word_bp_next=').replace('pl_reg<=','word_pl_next=')
    model_sync=model_sync.replace('bp_reg','bp').replace('words[ra]','read_value')
    model_sync=model_sync.replace('BLKSELB','BB').replace('BLK_SEL_1','SEL_B')
    text=f'''module relation_w{w}(
 input [13:0] ADA,ADB,K,input [4:0] read_lane,input [31:0] DI,
 input [2:0] BA,BB,SEL_A,SEL_B,input CEA,CEB,OCE,RESET,
 input old_bit,input [{w-1}:0] bp,pl,read_value);
wire [13:0] sdk_a={align}, sdk_b={align_b};
wire sdk_next={sdk_next};
wire word_next=(CEA && BA==SEL_A && (K>>{shift})==(ADA>>{shift})
 {'&& ADA[(K%'+str(w)+')/8]' if w>=16 else ''}) ? DI[K%{w}] : old_bit;
wire [13:0] sdk_read_bit=sdk_b+read_lane;
wire [13:0] word_read_bit=((ADB>>{shift})*{w})+read_lane;
wire [31:0] old_bp={{{{{32-w}{{1'b0}}}},bp}};
wire [31:0] old_pl={{{{{32-w}{{1'b0}}}},pl}};
reg [31:0] sdk_bp_next,sdk_pl_next;
reg [{w-1}:0] word_bp_next,word_pl_next;
{sdk_sync}
{model_sync}
always @* begin
 assert(sdk_next==word_next);
 assert(read_lane>={w} || sdk_read_bit==word_read_bit);
 assert(sdk_bp_next=={{{{{32-w}{{1'b0}}}},word_bp_next}});
 assert(sdk_pl_next=={{{{{32-w}{{1'b0}}}},word_pl_next}});
end
endmodule
'''
    return text,{'width':w,'write_bit_mapping':mapping,'read_offsets_msb_first':read_offsets}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--timeout',type=int,default=60);args=ap.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    source=sdk_body();model=word_model();rows=[];started=time.time()
    for width in (8,16,32):
        text,row=kernel(width);file=out/f'w{width}.v';file.write_text(text)
        commands=['read_verilog -formal '+quote(file),f'hierarchy -check -top relation_w{width}',
                  'proc','opt','chformal -lower','check -assert','sat -prove-asserts -verify -set-def-inputs']
        try:row['invocation']=run(commands,out,f'w{width}',timeout=max(1,args.timeout-(time.time()-started)));row['passed']=True
        except Exception as e:row.update(passed=False,error=repr(e))
        rows.append(row)
    init_sdk=re.search(r'reg \[16383:0\] ram_MEM\s*=\s*\{([^}]+)\}',source)[1]
    init_model=re.search(r'localparam \[16383:0\] IMAGE=\{([^}]+)\}',model)[1]
    init_order=[f'INIT_RAM_{i:02X}' for i in reversed(range(64))]
    init_ok=[x.strip() for x in init_sdk.split(',')]==[x.strip() for x in init_model.split(',')]==init_order
    init_ok=init_ok and 'words[i]=IMAGE[i*W +: W];' in model
    init_ok=init_ok and all(re.search(r'\b'+name+r'\s*=\s*0;',source) for name in ('bp_reg','pl_reg','bp_reg_sync','pl_reg_sync'))
    init_ok=init_ok and all(re.search(r'\b'+name+r'\s*=\s*0;',model) for name in ('bp_reg','pl_reg'))
    read_mode_ok="assign DO = (READ_MODE == 1'b0)? bp_reg: pl_reg;" in source and "assign DO={{(32-W){1'b0}}, (READ_MODE ? pl_reg : bp_reg)};" in model
    report={'scope':__doc__,'passed':all(r['passed'] for r in rows) and bool(init_ok) and read_mode_ok,
            'full_rtl_sec_proved':False,'production_admitted':False,
            'sdk_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'word_sha256':hashlib.sha256(model.encode()).hexdigest(),
            'init_layout_and_zero_output_checks':bool(init_ok),'read_mode_selection_check':read_mode_ok,
            'profiles':rows,'wall_seconds':time.time()-started}
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='profiles'},indent=2))
    if not report['passed']:raise SystemExit(1)


if __name__=='__main__':main()
