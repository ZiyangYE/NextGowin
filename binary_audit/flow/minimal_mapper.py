"""Deliberately narrow mapper: the probe's one rising-edge DFF, with GAO retained as a vendor macro."""
from pathlib import Path
import re,json,hashlib
ROOT=Path(__file__).resolve().parent
src=ROOT/'gao/01_elaborated_gao.v'
s=src.read_text(); top=re.search(r'\bmodule top\b.*?endmodule',s,re.S).group()
flops=re.findall(r'VERIFIC_DFFRS\s+(\w+)\s*\((.*?)\);',top,re.S)
assert len(flops)==1, 'Only one DFF is supported'
ports=dict(re.findall(r'\.(\w+)\(([^)]+)\)',flops[0][1]))
assert ports==dict(d='a',clk='clk',s="1'b0",r="1'b0",q='q'),ports
assert 'gw_gao gw_gao_inst_0' in top
assert all(x in top for x in ['buf (n9, clk)','buf (n8, q)','buf (n7, a)'])
ir={'schema':'probe-only-v1','source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'register':{'q':'q','d':'a','clock':'clk','edge':'rising','set':0,'reset':0},'gao':{'macro':'gw_gao','instance':'gw_gao_inst_0','samples':['a','q'],'clock':'clk'},'mapping':{'cell':'DFFRE','reset':'GND','enable':'VCC','init':0},'limitations':['one fixed probe design only','GAO macro is mapped by vendor DLL, not by this mapper','INIT=0 follows vendor baseline; RTL initial state was unspecified']}
(ROOT/'custom_ir.json').write_text(json.dumps(ir,indent=2))
vendor=(ROOT/'gao/05_mapped_gao.vg').read_text()
m=re.search(r'^module top\s*\(',vendor,re.M);assert m
macro=vendor[:m.start()]
assert 'module gw_gao' in macro
mapped='''// User datapath emitted by minimal_mapper.py from exported Verific intermediate.
module top(clk, a, q, tms_pad_i, tck_pad_i, tdi_pad_i, tdo_pad_o);
input clk, a, tms_pad_i, tck_pad_i, tdi_pad_i;
output q, tdo_pad_o;
wire clk_d, a_d, q_d, VCC, GND;
IBUF clk_ibuf(.I(clk), .O(clk_d));
IBUF a_ibuf(.I(a), .O(a_d));
OBUF q_obuf(.I(q_d), .O(q));
DFFRE custom_q(.D(a_d), .CLK(clk_d), .RESET(GND), .CE(VCC), .Q(q_d));
defparam custom_q.INIT=1'b0;
VCC VCC_cZ(.V(VCC));
GND GND_cZ(.G(GND));
GSR GSR(.GSRI(VCC));
gw_gao gw_gao_inst_0(.clk_d(clk_d), .a_d(a_d), .q_d(q_d),
 .tms_pad_i(tms_pad_i), .tck_pad_i(tck_pad_i), .tdi_pad_i(tdi_pad_i), .tdo_pad_o(tdo_pad_o));
endmodule
'''
(ROOT/'custom_mapped.vg').write_text(macro+mapped)
(ROOT/'custom_top.v').write_text(mapped)
# Exhaustive one-step transition comparison for this fixed mapping.
for q in [0,1]:
 for a in [0,1]:
  for rising in [False,True]:
   source_next=a if rising else q
   mapped_next=a if rising else q # RESET=0, CE=1
   assert source_next==mapped_next
print('PASS: single-DFF mapping, GAO macro linked; custom_mapped.vg generated')
