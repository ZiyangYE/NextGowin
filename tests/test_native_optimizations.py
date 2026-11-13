"""Validate native rewrites using the vendor's actual sequential models."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gw5a_dsp import choose,prepare,explicit_configuration
from optimize_native_json import optimize
from gowin_netlist_format import LIBRARY,name_unaliased_bits
from nextgowin import materialize
from yosys_runtime import SUITE,run,quote


def cell(kind,connections,parameters=None):
    output='Q' if kind.startswith('DFF') else 'F'
    return dict(type=kind,parameters=parameters or {},attributes={},connections={p:[b] for p,b in connections.items()},
                port_directions={p:'output' if p==output else 'input' for p in connections})


def sample():
    cells={'state':cell('DFFRE',dict(D=5,CLK=2,CE=3,RESET=4,Q=6),{'INIT':'1'})}
    for i in range(260):cells['load_'+str(i)]=cell('LUT2',dict(I0=6,I1=5,F=20+i),{'INIT':'1000'})
    cells.update(left=cell('LUT3',dict(I0=6,I1=5,I2=3,F=300),{'INIT':'11001010'}),
        right=cell('LUT3',dict(I0=5,I1=6,I2=3,F=301),{'INIT':'11001010'}),
        mux=cell('LUT3',dict(I0=300,I1=301,I2=4,F=302),{'INIT':'11001010'}),
        ng_acd_buf1=cell('LUT1',dict(I0=302,F=303),{'INIT':'10'}),
        ng_acd_buf2=cell('LUT1',dict(I0=303,F=304),{'INIT':'10'}))
    ports={n:dict(direction='input',bits=[b]) for n,b in [('clk',2),('ce',3),('rst',4),('d',5)]}
    ports['q']=dict(direction='output',bits=list(range(20,280))+[304])
    return dict(modules={'top':dict(attributes={},ports=ports,cells=cells,netnames={'state':dict(hide_name=0,bits=[6],attributes={'init':'1'})})})


def verilog(design,name):
    module=design['modules']['top'];lines=[f'module {name}(clk,ce,rst,d,q); input clk,ce,rst,d; output [260:0] q;']
    bits={b for c in module['cells'].values() for v in c['connections'].values() for b in v if isinstance(b,int)}
    lines.append('wire '+','.join('b'+str(b) for b in sorted(bits))+';')
    for port,row in module['ports'].items():
        if row['direction']=='input':lines.append(f'assign b{row["bits"][0]}={port};')
        else:
            for i,b in enumerate(row['bits']):lines.append(f'assign q[{i}]=b{b};')
    def signal(bit):return 'b'+str(bit) if isinstance(bit,int) else "1'b"+bit
    for i,c in enumerate(module['cells'].values()):
        params=', '.join('.'+k+'('+str(len(v))+"'b"+v+')' for k,v in c.get('parameters',{}).items())
        pins=', '.join('.'+p+'('+signal(bs[0])+')' for p,bs in c['connections'].items())
        lines.append(c['type']+(' #('+params+')' if params else '')+f' c{i}('+pins+');')
    return '\n'.join(lines+['endmodule'])


class NativeOptimizationsTests(unittest.TestCase):
    def test_replication_reference_names_anonymous_bits_before_json_reload(self):
        # A retained JSON-generated name plus a newly anonymous internal bit
        # reproduces the real large-design NEW_ID collision on this Yosys.
        d=dict(modules={'top':dict(attributes={},ports={'a':dict(direction='input',bits=[2]),
            'out':dict(direction='output',bits=[5])},netnames={
            '$auto$jsonparse.cc:561:json_import$1':dict(hide_name=1,bits=[4],attributes={})},
            cells={'one':cell('LUT1',dict(I0=2,F=4),{'INIT':'01'}),
                   'two':cell('LUT1',dict(I0=4,F=3),{'INIT':'01'}),
                   'three':cell('LUT1',dict(I0=3,F=5),{'INIT':'01'})})})
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as folder:
            p=Path(folder);mapping=p/'04_mapping';mapping.mkdir();source=mapping/'mapped.json'
            source.write_text(json.dumps(d))
            with self.assertRaises(RuntimeError):run(['read_json '+quote(source)],p,'unprotected')
            self.assertIn('count_id',(p/'unprotected.console.log').read_text())
            materialize(source,p/'05_netlist','top',optimize_resources=False,register_replication=True)
            self.assertTrue((p/'05_netlist/final.json').is_file())
            self.assertIn('actual_emitted_boundary_cec',json.loads((p/'05_netlist/native_replication.json').read_text()))

    def test_kept_architecture_leaf_is_not_repacked(self):
        d=sample();d['modules']['top']['cells']['mux']['attributes']['keep']='1'
        _,record=optimize(d)
        self.assertEqual(record['mux5_packs'],0)

    def test_json_anonymous_aliases_preserve_pins_and_existing_names(self):
        d=dict(modules={'top':dict(attributes={},ports={'out':dict(direction='output',bits=[5])},
            cells={'ng_json_bit_3':cell('LUT2',dict(I0=3,I1=4,F=5),{'INIT':'1000'})},
            netnames={'$auto$jsonparse.cc:561:json_import$1':dict(hide_name=1,bits=[4],attributes={})})})
        old=copy.deepcopy(d);rows=name_unaliased_bits(d)
        self.assertEqual(rows[0]['bits_named'],1)
        m=d['modules']['top'];self.assertEqual(m['cells'],old['modules']['top']['cells'])
        for n,r in old['modules']['top']['netnames'].items():self.assertEqual(m['netnames'][n],r)
        self.assertIn('ng_json_bit_3_1',m['netnames'])
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as folder:
            p=Path(folder);(p/'design.json').write_text(json.dumps(d))
            self.assertEqual(run(['read_json '+quote(p/'design.json')],p,'read')['returncode'],0)

    def test_native_models_preserve_initial_state_ce_reset_and_mux_truth(self):
        before=sample();after,record=optimize(before)
        self.assertEqual(record['identity_luts_removed'],2)
        self.assertEqual(record['mux5_packs'],1)
        self.assertGreater(record['registers_added'],0)
        self.assertEqual(before,sample(),'Optimizer mutated its input checkpoint')
        tb='''module tb;
reg clk=0,ce=0,rst=0,d=0; wire [260:0] a,b;
GSR GSR(.GSRI(1'b1)); gold g(clk,ce,rst,d,a); gate n(clk,ce,rst,d,b);
integer i; reg [31:0] seed=32'h8123;
initial begin
 #1; if(a!==b) $fatal(1,"initial mismatch");
 for(i=0;i<2048;i=i+1) begin
 clk=0;seed=seed*32'd1664525+32'd1013904223;ce=seed[9];rst=(i%17==0);d=seed[0];
 #2; if(a!==b) $fatal(1,"combinational mismatch");
 clk=1;#2;if(a!==b) $fatal(1,"sequential mismatch %0d",i);
 end $display("PASS native optimizations");$finish;
end endmodule'''
        env=dict(os.environ);env['PATH']=str(SUITE/'lib')+os.pathsep+env.get('PATH','')
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as folder:
            p=Path(folder);(p/'design.v').write_text(verilog(before,'gold')+'\n'+verilog(after,'gate')+'\n'+tb)
            command=[str(SUITE/'bin/iverilog.exe'),'-B',str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',(p/'sim').as_posix(),(p/'design.v').as_posix(),LIBRARY.as_posix()]
            cp=subprocess.run(command,env=env,capture_output=True,text=True,timeout=30);self.assertEqual(cp.returncode,0,cp.stderr)
            cp=subprocess.run([str(SUITE/'bin/vvp.exe'),'-M-','-M'+str(SUITE/'lib/ivl'),(p/'sim').as_posix()],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(cp.returncode,0,cp.stdout+cp.stderr);self.assertIn('PASS native optimizations',cp.stdout)

    def test_dsp_unsigned_sign_capacity_and_unknown_semantics(self):
        self.assertEqual(choose(12,False,12,False)[0],'MULTALU27X18')
        self.assertEqual(choose(12,True,12,True)[0],'MULT12X12')
        with self.assertRaises(ValueError):prepare('module mult_8u_9u(a,b,o);input [7:0]a;input [8:0]b;output [16:0]o;assign o=a+b;endmodule')

    def test_dsp_clock_binding_requires_every_register_to_be_bypassed(self):
        dsp=dict(type='MULTALU27X18',parameters={'C_SEL':'0'},connections={'CLK':['0','0']})
        design=dict(modules={'core':dict(attributes={},ports={'clk':dict(direction='input',bits=[2])},cells={'mul.ng_dsp':dsp})})
        rows=explicit_configuration(design)
        self.assertEqual(dsp['connections']['CLK'],[2,2])
        self.assertTrue(rows[0]['all_register_clocks_bypassed'])
        dsp['parameters']['AREG_CLK']='CLK0'
        with self.assertRaises(ValueError):explicit_configuration(design)

    def test_clock_and_unknown_initial_state_are_not_replicated(self):
        d=sample();d['modules']['top']['cells']['load_0']['connections']['CLK']=[6]
        d['modules']['top']['cells']['load_0']['port_directions']['CLK']='input'
        _,r=optimize(d);self.assertEqual(r['registers_added'],0)
        d=sample();d['modules']['top']['netnames']['state']['attributes']['init']='x'
        _,r=optimize(d);self.assertEqual(r['registers_added'],0)


if __name__=='__main__':unittest.main()
