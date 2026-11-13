import copy
from pathlib import Path
import sys
import tempfile
import unittest
import os
import subprocess
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from native_boundary_cec import prove,functions
from yosys_runtime import SUITE
from gowin_netlist_format import LIBRARY
from test_register_nextstate import sample


class NativeBoundaryContractTests(unittest.TestCase):
    def test_kept_internal_debug_signal_is_an_observed_boundary(self):
        source=sample();m=source['modules']['top']
        probe=copy.deepcopy(m['cells']['first']);probe['connections']['F']=[99000]
        m['cells']['debug_probe']=probe
        m['netnames']['debug_probe']=dict(bits=[99000],attributes={'keep':'1'})
        broken=copy.deepcopy(source)
        broken['modules']['top']['cells']['debug_probe']['parameters']['INIT']='0111'
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            with self.assertRaisesRegex(ValueError,'actual-cell CEC failed'):
                prove(source,broken,Path(temp)/'changed_debug')
            broken=copy.deepcopy(source);del broken['modules']['top']['netnames']['debug_probe']
            with self.assertRaisesRegex(ValueError,'Kept internal signal contract changed'):
                prove(source,broken,Path(temp)/'missing_debug')

    def test_all_alu_truth_modes_against_actual_native_simulation(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            p=Path(temp);lines=['`timescale 1ns/1ps','module tb; reg [3:0] a=15; wire [8:0] s,c; integer i;']
            checks=[]
            for mode in range(9):
                cell=dict(type='ALU',parameters={'ALU_MODE':format(mode,'04b')},connections=dict(I0=[2],I1=[3],I3=[4],CIN=[5],SUM=[6],COUT=[7]))
                truths=functions(cell)
                lines.append(f'ALU #(.ALU_MODE({mode})) u{mode}(.I0(a[0]),.I1(a[1]),.I3(a[2]),.CIN(a[3]),.SUM(s[{mode}]),.COUT(c[{mode}]));')
                checks.extend([f"if(s[{mode}]!==((16'h{truths[0][2]:04X}>>i)&1'b1)) $fatal(1,\"SUM mode {mode}\");",f"if(c[{mode}]!==((16'h{truths[1][2]:04X}>>i)&1'b1)) $fatal(1,\"COUT mode {mode}\");"])
            lines+=['initial begin for(i=0;i<16;i=i+1) begin a=i;#1;',*checks,'end $display("PASS 144 native ALU cases");$finish;end endmodule']
            (p/'tb.v').write_text('\n'.join(lines));env=dict(os.environ);env['PATH']=str(SUITE/'lib')+os.pathsep+env.get('PATH','')
            cp=subprocess.run([str(SUITE/'bin/iverilog.exe'),'-B',str(SUITE/'lib/ivl'),'-g2012','-s','tb','-o',str(p/'sim'),str(p/'tb.v'),str(LIBRARY)],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(cp.returncode,0,cp.stdout+cp.stderr)
            cp=subprocess.run([str(SUITE/'bin/vvp.exe'),'-M-','-M'+str(SUITE/'lib/ivl'),str(p/'sim')],env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(cp.returncode,0,cp.stdout+cp.stderr);self.assertIn('PASS 144 native ALU cases',cp.stdout)

    def test_signal_renumbering_preserves_state_and_real_cell_functions(self):
        source=sample();after=copy.deepcopy(source);m=after['modules']['top']
        for c in m['cells'].values():
            for p,bs in c['connections'].items():c['connections'][p]=[b+100 if isinstance(b,int) else b for b in bs]
        for r in list(m['ports'].values())+list(m['netnames'].values()):r['bits']=[b+100 if isinstance(b,int) else b for b in r['bits']]
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            p=Path(temp);r=prove(source,after,p/'valid');self.assertTrue(r[0]['cec']['equivalent'])
            broken=copy.deepcopy(after);broken['modules']['top']['cells']['first']['parameters']['INIT']='0111'
            with self.assertRaisesRegex(ValueError,'actual-cell CEC failed'):prove(source,broken,p/'wrong_lut')
            broken=copy.deepcopy(after);broken['modules']['top']['cells']['state']['parameters']['INIT']='0'
            with self.assertRaisesRegex(ValueError,'parameters changed'):prove(source,broken,p/'wrong_init')


if __name__=='__main__':unittest.main()
