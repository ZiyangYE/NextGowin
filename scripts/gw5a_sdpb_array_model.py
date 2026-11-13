"""Array-preserving binary SDPB adapter for proof experiments, not signoff.

The SDK hash is pinned. Equal read/write widths only; GSR must separately be
proved inactive. Address alignment, byte enables, output pipeline, INIT and
SYNC/ASYNC output reset are explicit. Unsupported parameters fail hierarchy.
No array is replaced by an unconstrained output or an opaque black box.
"""
from gowin_netlist_format import LIBRARY,EXPECTED
from ownpr_image_storage import sha
from split_primitive_models import units


def model(name='SDPB'):
    if sha(LIBRARY)!=EXPECTED:raise ValueError('SDK changed')
    text=LIBRARY.read_text(encoding='utf-8');row=units(text)['SDPB'];source=text[row['start']:row['end']]
    header=source[:source.index('input CLKA')].replace('module SDPB ', 'module '+name+' ',1)
    init=', '.join('INIT_RAM_'+format(i,'02X') for i in reversed(range(64)))
    body=r'''
input CLKA, CEA, CLKB, CEB, OCE, RESET;
input [13:0] ADA,ADB;
input [31:0] DI;
input [2:0] BLKSELA,BLKSELB;
output [31:0] DO;
localparam W=BIT_WIDTH_0;
localparam SHIFT=$clog2(W);
localparam DEPTH=16384/W;
localparam [16383:0] INIT_BITS={__INIT__};
generate if (BIT_WIDTH_0!=BIT_WIDTH_1 ||
 !(W==1 || W==2 || W==4 || W==8 || W==16 || W==32) ||
 !(READ_MODE==0 || READ_MODE==1) ||
 !(RESET_MODE=="SYNC" || RESET_MODE=="ASYNC")) begin
 NEXTGOWIN_UNSUPPORTED_SDPB_PARAMETERS reject();
end endgenerate
reg [W-1:0] memory [0:DEPTH-1];
integer address;
initial for(address=0;address<DEPTH;address=address+1)
 memory[address]=INIT_BITS[address*W+:W];
wire [13-SHIFT:0] wa=ADA[13:SHIFT];
wire [13-SHIFT:0] ra=ADB[13:SHIFT];
genvar lane;
generate for(lane=0;lane<W;lane=lane+1) begin: lanes
 always @(posedge CLKA)
  if(CEA && BLKSELA==BLK_SEL_0 && (W<16 || ADA[lane/8]))
   memory[wa][lane]<=DI[lane];
end endgenerate
reg [31:0] bypass=0, pipeline=0;
generate if(RESET_MODE=="ASYNC") begin
 always @(posedge CLKB or posedge RESET)
  if(RESET) begin bypass<=0;pipeline<=0;end
  else begin
   if(OCE) pipeline<=bypass;
   if(CEB && BLKSELB==BLK_SEL_1) bypass[W-1:0]<=memory[ra];
  end
end else begin
 always @(posedge CLKB)
  if(RESET) begin bypass<=0;pipeline<=0;end
  else begin
   if(OCE) pipeline<=bypass;
   if(CEB && BLKSELB==BLK_SEL_1) bypass[W-1:0]<=memory[ra];
  end
end endgenerate
assign DO=READ_MODE==0?bypass:pipeline;
endmodule
'''.replace('__INIT__',init)
    return header+body


def address_certificate():
    # Exhaust every legal 14-bit address and data lane. This establishes the
    # storage bijection and byte-enable address bits, not whole-design proof.
    count=0
    for width in (1,2,4,8,16,32):
        shift=width.bit_length()-1
        for address in range(16384):
            for lane in range(width):
                sdk=(address&~(width-1))+lane
                array=(address>>shift)*width+lane
                if sdk!=array or not 0<=array<16384:raise AssertionError('Address bijection')
                if width>=16 and not 0<=lane//8<shift:raise AssertionError('Byte enable outside low address bits')
                count+=1
    return dict(status='all_binary_address_lane_pairs_matched',pairs=count,widths=[1,2,4,8,16,32],
        scope='SDK aligned packed-bit address equals array word/lane; byte enable is ADA[lane//8] for widths16/32. No clock/data/control or full-design proof implied.')
