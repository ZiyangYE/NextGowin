"""SDK-derived symmetric SDPB word-array model for bounded formal experiments.

Not enabled in production source-proof admission until its full certificate is
accepted. The global reset is the existing constant-inactive proof contract.
No Gold initialization is changed; RAM/outputs retain explicit SDK initial state.
"""
import re
from gowin_netlist_format import LIBRARY,EXPECTED
from split_primitive_models import units
from ownpr_image_storage import sha


def sdk_body():
    if sha(LIBRARY)!=EXPECTED:raise ValueError('SDK memory model changed')
    text=LIBRARY.read_text(encoding='utf-8');row=units(text)['SDPB']
    return text[row['start']:row['end']]


def word_model():
    body=sdk_body();params=re.findall(r'^parameter\b[^;]*;',body,re.M)
    expected={f'INIT_RAM_{i:02X}' for i in range(64)}
    actual={re.match(r'parameter\s+(\w+)',p)[1] for p in params}
    if actual!=expected|{'READ_MODE','BIT_WIDTH_0','BIT_WIDTH_1','BLK_SEL_0','BLK_SEL_1','RESET_MODE'}:
        raise ValueError('Unexpected SDK SDPB parameters')
    init=', '.join(f'INIT_RAM_{i:02X}' for i in reversed(range(64)))
    return '''module SDPB (DO, DI, BLKSELA, BLKSELB, ADA, ADB, CLKA, CLKB, CEA, CEB, OCE, RESET);
'''+ '\n'.join(params)+'''
input [31:0] DI;
input [13:0] ADA, ADB;
input [2:0] BLKSELA, BLKSELB;
input CLKA, CLKB, CEA, CEB, OCE, RESET;
output [31:0] DO;
generate if (BIT_WIDTH_0 != BIT_WIDTH_1 ||
    !(BIT_WIDTH_0==1 || BIT_WIDTH_0==2 || BIT_WIDTH_0==4 || BIT_WIDTH_0==8 || BIT_WIDTH_0==16 || BIT_WIDTH_0==32) ||
    !(READ_MODE==0 || READ_MODE==1) || RESET_MODE!="SYNC") begin: unsupported
    UNSUPPORTED_SDPB_PROOF_PROFILE reject();
end else begin: supported
    localparam W=BIT_WIDTH_0;
    localparam SHIFT=$clog2(W);
    localparam DEPTH=16384/W;
    localparam [16383:0] IMAGE={'''+init+'''};
    reg [W-1:0] words [0:DEPTH-1];
    reg [W-1:0] bp_reg, pl_reg;
    integer i;
    initial begin
        bp_reg=0; pl_reg=0;
        for(i=0;i<DEPTH;i=i+1) words[i]=IMAGE[i*W +: W];
    end
    wire [13:0] wa=ADA>>SHIFT;
    wire [13:0] ra=ADB>>SHIFT;
    if(W<16) begin: narrow
        always @(posedge CLKA)
            if(CEA && BLKSELA==BLK_SEL_0) words[wa]<=DI[W-1:0];
    end else begin: bytes
        genvar lane;
        for(lane=0;lane<W/8;lane=lane+1) begin: byte_lane
            always @(posedge CLKA)
                if(CEA && BLKSELA==BLK_SEL_0 && ADA[lane])
                    words[wa][lane*8 +: 8]<=DI[lane*8 +: 8];
        end
    end
    always @(posedge CLKB) begin
        if(RESET) begin bp_reg<=0; pl_reg<=0; end
        else begin
            if(OCE) pl_reg<=bp_reg;
            if(CEB && BLKSELB==BLK_SEL_1) bp_reg<=words[ra];
        end
    end
    assign DO={{(32-W){1'b0}}, (READ_MODE ? pl_reg : bp_reg)};
end endgenerate
endmodule
'''


def normalized_packed_reference():
    """Replace only contiguous write concatenations with equal indexed slices.

    This is an experimental Yosys frontend normalization, not a proof itself.
    Icarus comparisons must also use the original unnormalized SDK reference.
    """
    body=sdk_body().replace('wire grstn = GSR.GSRO;',"wire grstn = 1'b1;")
    pattern=r'\{([^{}]+)\}\s*<=\s*DI\[([^\]]+)\];'
    changes=[]
    def replace(m):
        terms=[x.strip() for x in m[1].split(',')];indices=[]
        for term in terms:
            q=re.fullmatch(r'ram_MEM\[addr_a(?:\+(\d+))?\]',term)
            if not q:raise ValueError('Unrecognized SDK memory write')
            indices.append(int(q[1] or 0))
        lo=min(indices);width=len(indices)
        if indices!=list(range(lo+width-1,lo-1,-1)):raise ValueError('Noncontiguous SDK write')
        changes.append(dict(low=lo,width=width,data_slice=m[2]))
        return f'ram_MEM[addr_a+{lo} +: {width}] <= DI[{m[2]}];'
    result,count=re.subn(pattern,replace,body)
    if count!=9:raise ValueError('SDK write inventory changed: '+str(count))
    return result,changes
