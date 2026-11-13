from pathlib import Path
import re,json,hashlib,xml.etree.ElementTree as E
R=Path(__file__).resolve().parent
paths=['gao/01_elaborated_gao.v','gao/02_reloaded_gao.v','gao/05_mapped_gao.vg','gao/06_reloaded_mapped.vg','custom_ir.json','custom_top.v','custom_mapped.vg','direct_pnr/ao_0.fs','direct_pnr/ao_0.bin','direct_pnr/custom_mapped.vo','direct_pnr/custom_mapped.tr','direct_pnr/custom_mapped.rpt.txt','inputs/probe.rao','probe.sdc']
files={}
for name in paths:
 p=R/name;assert p.is_file() and p.stat().st_size>0,name
 files[name]={'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
for name in ['gao/gao_steps.log','gao/resume_from_export.log','direct_pnr.log']:
 s=(R/name).read_text(errors='replace');assert not re.search(r'^ERROR\s*\(',s,re.M),name
log=(R/'direct_pnr.log').read_text(errors='replace')
assert 'PNR_RESULT 0' in log and 'Bitstream generation completed' in log
assert 'GowinSynthesis start' not in log and 'Running parser ...' not in log
inv=json.loads((R/'direct_pnr/invocation.json').read_text())
assert inv['input_sha256_before']==inv['input_sha256_after']==files['custom_mapped.vg']['sha256']
assert (R/'direct_pnr/ao_0.fs').stat().st_mtime >= inv['started_epoch']-2
vo=(R/'direct_pnr/custom_mapped.vo').read_text(errors='replace')
assert re.search(r'DFFRE\s+custom_q\s*\(',vo) and 'gw_gao_inst_0/' in vo and 'GW_JTAG' in vo
mid=(R/'gao/01_elaborated_gao.v').read_text()
assert 'VERIFIC_DFFRS q_c' in mid and 'gw_gao gw_gao_inst_0' in mid
tr=(R/'direct_pnr/custom_mapped.tr').read_text(errors='replace')
assert '<Numbers of Setup Violated Endpoints>:0' in tr
assert '<Numbers of Hold Violated Endpoints>:0' in tr
for clk in ['sample_clk','jtag_clk']:
 for kind in ['setup','hold']:
  assert re.search(r'\b'+clk+r'\s+'+kind+r'\s+0\.000\s+0\b',tr)
aoid=E.parse(R/'inputs/probe.rao').findtext('GAO_ID')
cmd=(R/'direct_pnr/cmd.do').read_text();assert '-rtl_gao' in cmd and '-gao_crc '+aoid in cmd
report={'status':'PASS','scope':'One-register RTL GAO demo; custom user-datapath mapper + vendor-mapped GAO macro','intermediate_export':True,'fresh_process_reload':True,'custom_cell_in_post_route_netlist':True,'pnr_input_unchanged':True,'bitstream_generated':True,'setup_violations':0,'hold_violations':0,'gao_id':aoid,'board_programmed':False,'physical_pin_constraints':False,'files':files}
(R/'verification.json').write_text(json.dumps(report,indent=2))
print('PASS: intermediate exported and reloaded; custom DFFRE survived P&R; GAO/JTAG retained; fresh bitstream generated; setup/hold violations = 0.')
print('Limit: one-register prototype, vendor-mapped GAO macro, automatic pin placement, no hardware capture validation.')
