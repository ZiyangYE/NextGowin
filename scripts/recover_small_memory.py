"""Recover the read-tag array behind Verific's per-bit FF lowering.

This bounded behavioral recovery is accepted only after sequential equivalence
against the actual exported module. Additional GAO ports reject the match.
"""
import hashlib
from pathlib import Path
import re
from verific_bridge import units
from yosys_runtime import ROOT,run,quote


def recover(text,out):
    records=[];source=ROOT/'FPGAFLY_ISCAS2027/rtl/memory/read_tags.v'
    for name,(a,b) in units(text).items():
        match=re.fullmatch(r'\\read_tags\(DEPTH=(\d+)\)',name)
        if not match:continue
        depth=int(match[1]);body=text[a:b]
        ports=re.findall(r'^\s*(?:input|output)\s+(\w+)\s*;',body,re.M)
        expected={'clk','rst','issue','issue_stream','response_valid','direct_response','stream_response'}
        if set(ports)!=expected:raise ValueError('Read-tag recovery rejects changed/instrumented interface')
        rtl=source.read_text();rtl,n=re.subn(r'parameter DEPTH\s*=\s*\d+',f'parameter DEPTH = {depth}',rtl,count=1)
        if n!=1:raise ValueError('Read-tag depth parameter missing')
        declaration='reg [DEPTH-1:0] tags = 0;'
        if rtl.count(declaration)!=1:raise ValueError('Read-tag behavioral array declaration changed')
        rtl=rtl.replace(declaration,'(* ram_style="distributed" *) reg ng_tags [0:DEPTH-1];\n    (* keep=1 *) wire [DEPTH-1:0] tags;\n    genvar ng_i; generate for(ng_i=0;ng_i<DEPTH;ng_i=ng_i+1) begin\n        initial ng_tags[ng_i]=0;\n        assign tags[ng_i]=ng_tags[ng_i];\n    end endgenerate')
        # All dynamic accesses switch to the recovered array; alias `tags`
        # preserves the exported state correspondence for the proof.
        rtl=re.sub(r'(?<!ng_)\btags\[(?!ng_i\])', 'ng_tags[',rtl)
        gate=re.sub(r'\bmodule\s+read_tags\b','module gate',rtl,count=1)
        folder=out/('memory_'+str(len(records)));folder.mkdir(parents=True,exist_ok=False)
        (folder/'gold.v').write_text(text);(folder/'gate.v').write_text(gate)
        target=name
        invocation=run(['read_verilog -sv '+quote(folder/'gold.v'),f'hierarchy -top {target}',
            'flatten','proc','opt','memory_map','opt',f'rename {target} gold','design -stash gold',
            'read_verilog -sv '+quote(folder/'gate.v'),'hierarchy -top gate','proc','opt','memory_map','opt','design -stash gate',
            'design -copy-from gold gold','design -copy-from gate gate','equiv_make -inames gold gate equiv',
            'hierarchy -top equiv','equiv_simple -seq 4','equiv_induct -seq 4','equiv_status -assert'],folder,'proof',timeout=120)
        # Avoid replacement-string backslash interpretation for escaped names.
        implementation=rtl.replace('(* keep=1 *) wire [DEPTH-1:0] tags;','').replace('assign tags[ng_i]=ng_tags[ng_i];','')
        replacement=re.sub(r'\bmodule\s+read_tags\b',lambda m:'module '+name+' ',implementation,count=1)
        text=text[:a]+replacement+text[b:]
        records.append(dict(module=name,depth=depth,source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            exported_module_sha256=hashlib.sha256(body.encode()).hexdigest(),proof=invocation,
            proof_inputs_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [folder/'gold.v',folder/'gate.v']},
            scope='Behavioral tag array recovered only after sequential equivalence against the exported module; no added GAO interface.'))
        # The supported project has one specialization; guard index stability.
        break
    return text,records

