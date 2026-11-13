import copy
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from insert_gowin_pads import insert
from map_resource_json import FUNCTIONS,extract,replace,readback
from gowin_blif import read_blif
from run_abc_reproduction import cec
from verific_bridge import convert,units
from yosys_runtime import SUITE
from gowin_netlist_format import complete_output_ports,normalize,legalize_gw5a_aliases,bind_net_aliases,LIBRARY


def cell(kind,pins,dirs=None):
    return dict(type=kind,parameters={},attributes={},hide_name=0,connections=pins,
                port_directions=dirs or {p:'output' if p in ('Y','F','Q','O') else 'input' for p in pins})


class CPUFlowTest(unittest.TestCase):
    def test_constant_package_ports_do_not_rewire_internal_literals(self):
        module=dict(ports={'q':dict(direction='output',bits=['0','1']),
                           'unused':dict(direction='input',bits=['0'])},
            netnames={},cells={'gate':cell('$_AND_',dict(A=['0'],B=['1'],Y=[9]))})
        actual,record=insert(dict(modules={'top':module}),'top');m=actual['modules']['top']
        self.assertEqual(m['cells']['gate']['connections'],module['cells']['gate']['connections'])
        self.assertEqual(len(record['inserted_pads']),3)
        q=m['ports']['q']['bits'];self.assertEqual(len(set(q)),2)
        for i,b in enumerate(q):
            pad=next(c for c in m['cells'].values() if c['type']=='OBUF' and c['connections']['O']==[b])
            self.assertEqual(pad['connections']['I'],[str(i)])

    def test_aliased_output_and_input_have_distinct_package_pads(self):
        module=dict(ports={'z':dict(direction='output',bits=[2]),'a':dict(direction='input',bits=[2]),
                           'zz':dict(direction='output',bits=[2])},netnames={},cells={})
        actual,record=insert(dict(modules={'top':module}),'top');m=actual['modules']['top']
        self.assertEqual(len(record['inserted_pads']),3)
        self.assertEqual(len({r['bits'][0] for r in m['ports'].values()}),3)
        ibuf=next(c for c in m['cells'].values() if c['type']=='IBUF')
        self.assertEqual([c['connections']['I'] for c in m['cells'].values() if c['type']=='OBUF'],[ibuf['connections']['O']]*2)

    def test_frontend_rejects_another_device_before_reading_sources(self):
        from export_gowin_frontend import project_inputs
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            project=Path(temp)/'wrong.prj'
            project.write_text('<Project><Device partNumber="another-device"/></Project>')
            with self.assertRaisesRegex(ValueError,'pinned to'):
                project_inputs(project)

    def test_legacy_constraint_alias_keeps_signal_connections(self):
        top=dict(ports={'clk':dict(direction='input',bits=[2])},
            netnames={'host.selected':dict(bits=[3],attributes={}), 'selected.q':dict(bits=[3],attributes={})},
            cells={'ff':cell('DFFRE',dict(D=[4],Q=[3],CLK=[2],CE=['1'],RESET=['0']))})
        design=dict(modules={'top':top});before=copy.deepcopy(top['cells'])
        bind_net_aliases(design,{'selected_Z':dict(module='top',net='host.selected')})
        self.assertEqual(top['cells'],before)
        self.assertEqual(top['netnames']['selected_Z']['bits'],[3])
        self.assertNotIn('host.selected',top['netnames'])
        top['netnames']['clock']=dict(bits=[2],attributes={})
        with self.assertRaises(ValueError):bind_net_aliases(design,{'external':dict(module='top',net='clock')})

    def test_resume_rechecks_actual_lut_truth_after_rehash(self):
        source=ROOT/'results/cpu_gao_flow_011'
        if not (source/'04_mapping/mapped.json').exists():self.skipTest('Hybrid flow fixture unavailable')
        from check_flow_checkpoint import check
        from run_abc_reproduction import sha
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            out=Path(temp)
            for name in ['01_frontend','02_bridge','03_resources','04_mapping']:
                shutil.copytree(source/name,out/name)
            manifest=json.loads((source/'manifest.json').read_text())
            cfg=out/'config.json';cfg.write_text(json.dumps(manifest['config'],indent=2)+'\n')
            manifest['config_sha256']=sha(cfg)
            (out/'manifest.json').write_text(json.dumps(manifest))
            check(out,cfg)
            path=out/'04_mapping/mapped.json';design=json.loads(path.read_text())
            lut=next(c for m in design['modules'].values() for n,c in m.get('cells',{}).items()
                     if n.startswith('ng_acd_') and c['type'].startswith('LUT'))
            old=lut['parameters']['INIT'];lut['parameters']['INIT']=('1' if old[0]=='0' else '0')+old[1:]
            path.write_text(json.dumps(design))
            saved=out/'04_mapping/manifest.json';row=json.loads(saved.read_text());row['output_sha256']=sha(path);saved.write_text(json.dumps(row))
            with self.assertRaisesRegex(ValueError,'Actual mapped cells disagree'):
                check(out,cfg)

    def test_native_parameter_formats_and_unset_strings(self):
        source="""module top();
  ALU add (.SUM(y));
  defparam add.ALU_MODE = 32'd2;
  DPB ram (.DOA(q));
  defparam ram.BLK_SEL_0 = 3'h0;
  defparam ram.WRITE_MODE0 = 1;
  PLL pll (.CLKOUT0(clk));
  defparam pll.VR_EN = {0{1'b0}};
endmodule
"""
        actual,record=normalize(source)
        self.assertIn('add.ALU_MODE = 2;',actual)
        self.assertIn("ram.BLK_SEL_0 = 3'b000;",actual)
        self.assertIn("ram.WRITE_MODE0 = 2'b01;",actual)
        self.assertNotIn('pll.VR_EN',actual)
        self.assertEqual(len(record['parameter_format_changes']),4)

    def test_optional_native_outputs_are_connected_and_kept(self):
        primitive=dict(attributes={'blackbox':'1'},ports={'I':dict(direction='input',bits=[2]),
            'O':dict(direction='output',bits=[3]),'UNUSED':dict(direction='output',bits=[4])})
        top=dict(attributes={},netnames={},cells={'ip':cell('ip',dict(I=[100],O=[101]))})
        design=dict(modules={'ip':primitive,'top':top})
        empty_design=copy.deepcopy(design)
        changes=complete_output_ports(design)
        self.assertEqual(len(changes),1)
        self.assertGreater(top['cells']['ip']['connections']['UNUSED'][0],101)
        self.assertTrue(next(iter(top['netnames'].values()))['attributes']['keep'])
        empty_design['modules']['top']['cells']['ip']['connections']['UNUSED']=[]
        changes=complete_output_ports(empty_design)
        self.assertEqual(len(changes),1)
        self.assertEqual(len(empty_design['modules']['top']['cells']['ip']['connections']['UNUSED']),1)

    def test_gw5a_dffe_alias_matches_installed_ff_models(self):
        design=dict(modules={'top':dict(cells={'f':cell('DFFE',dict(D=[2],Q=[3],CLK=[4],CE=[5]))})})
        record=legalize_gw5a_aliases(design)
        self.assertEqual(record[0]['after'],'DFFRE')
        actual=design['modules']['top']['cells']['f']
        self.assertEqual(actual['connections']['RESET'],['0'])
        tb='''`timescale 1ns/1ps
module tb;
reg clk=0,d=0,ce=0; wire a0,a1,b0,b1;
GSR GSR(.GSRI(1'b1));
DFFE #(.INIT(1'b0)) gold0(.D(d),.CLK(clk),.CE(ce),.Q(a0));
DFFE #(.INIT(1'b1)) gold1(.D(d),.CLK(clk),.CE(ce),.Q(a1));
'''+actual['type']+''' #(.INIT(1'b0)) gate0(.D(d),.CLK(clk),.CE(ce),.RESET(1'b0),.Q(b0));
'''+actual['type']+''' #(.INIT(1'b1)) gate1(.D(d),.CLK(clk),.CE(ce),.RESET(1'b0),.Q(b1));
integer i;
initial begin
#1; if(a0!==b0 || a1!==b1) $fatal(1,"initial state");
for(i=0;i<32;i=i+1) begin
clk=0;ce=i%2;d=(i/2)%2;#2;clk=1;#1;
if(a0!==b0 || a1!==b1) $fatal(1,"transition");
end
$display("PASS aliases");$finish;
end endmodule
'''
        iv=SUITE/'bin/iverilog.exe';vvp=SUITE/'bin/vvp.exe'
        if not iv.exists():self.skipTest('Project-local simulator missing')
        env=dict(os.environ);env['PATH']=str(SUITE/'lib')+os.pathsep+env['PATH']
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            p=Path(temp)
            # GW5A has no physical DFFE. Use the pinned open synthesis model
            # for the source alias and the installed vendor GW5A DFFRE model.
            source_model=(SUITE/'share/yosys/gowin/cells_sim.v').read_text()
            model=re.search(r'\bmodule DFFE\b.*?\bendmodule\b',source_model,re.S)[0]
            (p/'tb.v').write_text(model+'\n'+tb)
            cp=subprocess.run([str(iv),'-B',str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',(p/'sim').as_posix(),LIBRARY.as_posix(),(p/'tb.v').as_posix()],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(cp.returncode,0,cp.stderr)
            cp=subprocess.run([str(vvp),'-M-','-M'+str(SUITE/'lib/ivl'),(p/'sim').as_posix()],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(cp.returncode,0,cp.stdout+cp.stderr)
            self.assertIn('PASS aliases',cp.stdout)

    def test_vendor_specialization_and_nonzero_init_survive(self):
        text='''module top(clk,q);
input clk;
output q /* Init Val: 1 */ /* verific syn_keep=1 */ ;
\\LUT2(INIT=4'h6) gate (.F(q),.I0(clk),.I1(1'b0));
endmodule
module \\LUT2(INIT=4'h6) (F,I0,I1) /* verific cell_name=LUT2 */ ; // installed/prim_syn.v
output F;
input I0;
input I1;
// parameter INIT = 4'b0110 ;
// parameter ID = "" ;
endmodule
'''
        transformed,record=convert(text)
        self.assertIn("(* init = 1'b1 *)",transformed)
        self.assertIn('(* keep = 1 *)',transformed)
        self.assertIn("LUT2 #(.INIT(4'b0110)) gate",transformed)
        self.assertEqual(len(record['initial_values']),1)
        self.assertIn("parameter INIT = 4'b0110",record['vendor_library_verilog'])
        self.assertNotIn('module \\LUT2',transformed)
        with self.assertRaises(ValueError):convert(text.replace('Init Val: 1','Init Val: invalid'))

    def test_quoted_escaped_module_names_remain_distinct(self):
        text='module \\PLL(FCLKIN="50") (a); input a; endmodule\nmodule \\PLL(FCLKIN="64") (a); input a; endmodule\n'
        self.assertEqual(len(units(text)),2)

    def test_lut_readback_detects_corrupt_truth_and_preserves_state_cells(self):
        module=dict(ports={'a':dict(direction='input',bits=[2]),'b':dict(direction='input',bits=[3]),
                           'q':dict(direction='output',bits=[6])},
            netnames={n:dict(bits=[b],attributes={}) for n,b in [('a',2),('b',3),('m',5),('q',6)]},
            cells={'and':cell('$_AND_',dict(A=[2],B=[3],Y=[5])),
                   'ff':cell('DFFRE',dict(D=[5],CLK=[2],Q=[6],CE=['1'],RESET=['0']))})
        cut=extract(module)
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            folder=Path(temp);source=folder/'source.blif';source.write_text(cut['blif'])
            candidate=replace(module,cut,read_blif(source))
            self.assertEqual(candidate['cells']['ff'],module['cells']['ff'])
            back=folder/'back.blif';back.write_text(readback(candidate,cut))
            abc=ROOT/'build/abc/_TEST/abc.exe'
            if not abc.exists():self.skipTest('Pinned ABC unavailable')
            self.assertTrue(cec(abc,source,back,folder/'good.log',30)['equivalent'])
            target=next(c for c in candidate['cells'].values() if c['type']=='LUT2')
            target['parameters']['INIT']='0000'
            back.write_text(readback(candidate,cut))
            self.assertFalse(cec(abc,source,back,folder/'bad.log',30)['equivalent'])

    def test_pad_nets_do_not_collide_with_anonymous_mapper_bits(self):
        module=dict(attributes={},ports={'a':dict(direction='input',bits=[2]),'q':dict(direction='output',bits=[3])},
            netnames={'a':dict(bits=[2],attributes={}), 'q':dict(bits=[3],attributes={})},
            cells={'one':cell('LUT1',dict(I0=[2],F=[100])), 'two':cell('LUT1',dict(I0=[100],F=[3]))})
        actual,record=insert(dict(modules={'top':module}),'top')
        self.assertEqual(len(record['inserted_pads']),2)
        pads=[c for c in actual['modules']['top']['cells'].values() if c['type']=='IBUF']
        self.assertGreater(pads[0]['connections']['O'][0],100)
        self.assertEqual(actual['modules']['top']['cells']['one']['connections']['F'],[100])
        self.assertEqual(module['cells']['one']['connections']['I0'],[2])

    def test_existing_pad_inside_ip_is_not_duplicated(self):
        top=dict(attributes={},ports={'a':dict(direction='input',bits=[2]),'q':dict(direction='output',bits=[3])},
            netnames={'a':dict(bits=[2],attributes={}), 'q':dict(bits=[3],attributes={})},
            cells={'ip':cell('ip',dict(a=[2],q=[3]),dict(a='input',q='output'))})
        ip=dict(attributes={},ports={'a':dict(direction='input',bits=[2]),'q':dict(direction='output',bits=[3])},
            netnames={},cells={'in':cell('IBUF',dict(I=[2],O=[4])), 'out':cell('OBUF',dict(I=[4],O=[3]))})
        _,record=insert(dict(modules=dict(top=top,ip=ip)),'top')
        self.assertEqual(record['inserted_pads'],[])
        self.assertEqual(len(record['preserved_hierarchical_pads']),2)

    def test_memory_reconstruction_matches_linear_export(self):
        helper='''module ClockedWritePort_2_3_0_3_2_0 (clk,write_enable,write_address,write_data,Ram);
input clk,write_enable;
input [1:0] write_address;
input [2:0] write_data;
output [11:0] Ram;
reg [11:0] Ram;
always @(posedge clk) if(write_enable) Ram[-(write_address[1:0]-(3))*3 +: 3] <= write_data;
endmodule
module ReadPort_2_3_0_3_2_0 (read_enable,read_address,read_data,Ram);
input read_enable;
input [1:0] read_address;
output [2:0] read_data;
reg [2:0] read_data;
input [11:0] Ram;
always @(read_enable,read_address,Ram) if(read_enable) read_data = Ram[-(read_address[1:0]-(3))*3 +: 3];
endmodule
'''
        top='''module user(clk,we,wa,ra,d,q);
input clk,we; input [1:0] wa,ra; input [2:0] d; output [2:0] q;
wire [11:0] ram /* verific no_rw_check=1, ram_style="block" */;
ClockedWritePort_2_3_0_3_2_0 w(.clk(clk),.write_enable(we),.write_address(wa),.write_data(d),.Ram(ram));
ReadPort_2_3_0_3_2_0 r(.read_enable(1'b1),.read_address(ra),.read_data(q),.Ram(ram));
endmodule
'''
        transformed,record=convert(top+helper)
        self.assertEqual(len(record['restored_memories']),1)
        self.assertEqual(record['restored_memories'][0]['restored_attributes'],{'ram_style':'block','no_rw_check':1})
        self.assertIn('ram_style = "block", no_rw_check = 1',transformed)
        self.assertNotIn('module ClockedWritePort',transformed)
        iv=SUITE/'bin/iverilog.exe';vvp=SUITE/'bin/vvp.exe'
        if not iv.exists():self.skipTest('Project-local Icarus unavailable')
        tb='''module tb;
reg clk=0,we=1; reg [1:0] wa=0,ra=0; reg [2:0] d=0; wire [2:0] a,b;
gold g(clk,we,wa,ra,d,a); gate n(clk,we,wa,ra,d,b);
integer i,j;
initial begin
for(i=0;i<128;i=i+1) begin
 clk=0; wa=i%4; d=(i/4)%8; #1; clk=1; #1;
 for(j=0;j<4;j=j+1) begin ra=j; #1; if(a!==b) $fatal(1,"memory mismatch"); end
end
$display("PASS memory"); $finish;
end endmodule
'''
        env=dict(os.environ);env['PATH']=str(SUITE/'lib')+os.pathsep+env['PATH']
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            p=Path(temp);(p/'design.v').write_text((top+helper).replace('module user(','module gold(',1)+'\n'+transformed.replace('module user(','module gate(',1)+tb)
            cp=subprocess.run([str(iv),'-B',str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',(p/'sim').as_posix(),(p/'design.v').as_posix()],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(cp.returncode,0,cp.stderr)
            cp=subprocess.run([str(vvp),'-M-','-M'+str(SUITE/'lib/ivl'),(p/'sim').as_posix()],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(cp.returncode,0,cp.stdout+cp.stderr)
            self.assertIn('PASS memory',cp.stdout)


if __name__=='__main__':unittest.main()
