from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from gowin_cones import parse,replace,readback
from run_abc_reproduction import cec
from audit_project_remap import check_statements,skeleton


SAMPLE='''module test(a,b,clk,q,d);
input a,b,clk;
output q,d;
wire t,ck;
LUT2 comb(.I0(a),.I1(b),.F(t));
defparam comb.INIT=4'h6;
LUT1 clock_gate(.I0(clk),.F(ck));
defparam clock_gate.INIT=2'h2;
DFFRE state(.D(t),.CLK(ck),.CE(t),.RESET(1'b0),.Q(q));
defparam state.INIT=1'b1;
assign d=t;
endmodule
module other(a,y);
input a;output y;assign y=a;
endmodule
'''


class ConeTest(unittest.TestCase):
    def test_clock_cone_and_state_preserved_and_actual_init_rechecked(self):
        binary=ROOT/'build/abc/_TEST/abc.exe'
        if not binary.exists():self.skipTest('ABC not built')
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);source=folder/'source.blif';back=folder/'back.blif'
            parsed=parse(SAMPLE,'test');source.write_text(parsed['blif'])
            self.assertEqual(len(parsed['luts']),1)
            self.assertEqual(parsed['protected_luts'],1)
            candidate,check,record=replace(SAMPLE,parsed,source,source)
            self.assertIn("defparam state.INIT=1'b1;",candidate)
            self.assertIn("LUT1 clock_gate(.I0(clk),.F(ck));",candidate)
            self.assertEqual(candidate[candidate.index('module other'):],SAMPLE[SAMPLE.index('module other'):])
            check_statements(parsed,check)
            with self.assertRaises(ValueError):
                check_statements(parsed,parse(candidate.replace("state.INIT=1'b1","state.INIT=1'b0"),'test'))
            altered=candidate.replace('module other(a,y);','module other(a,y,z);')
            self.assertNotEqual(skeleton(SAMPLE,{'test'}),skeleton(altered,{'test'}))
            back.write_text(readback(check,parsed))
            self.assertTrue(cec(binary,source,back,folder/'cec.log',30)['equivalent'])
            bad=candidate.replace("INIT=4'h6","INIT=4'h7")
            back.write_text(readback(parse(bad,'test'),parsed))
            self.assertFalse(cec(binary,source,back,folder/'bad.log',30)['equivalent'])

    def test_escaped_scalar_and_escaped_bus_bit_are_distinct(self):
        text='''module test(\\scope.x ,\\scope.x[1] ,y);
input [1:0] \\scope.x ;
input \\scope.x[1] ;
output y;
LUT2 l(.I0(\\scope.x [1]),.I1(\\scope.x[1] ),.F(y));
defparam l.INIT=4'h6;
endmodule'''
        parsed=parse(text,'test')
        self.assertEqual(parsed['inputs'],['\\scope.x [1]','\\scope.x[1]'])
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'source.blif';source.write_text(parsed['blif'])
            candidate,check,_=replace(text,parsed,source,source)
            self.assertEqual(check['inputs'],parsed['inputs'])

    def test_new_dependency_and_behavioral_syntax_rejected(self):
        text='''module test(a,b,y);input a,b;output y;wire dead;
LUT1 x(.I0(a),.F(y));defparam x.INIT=2'h2;
LUT1 z(.I0(b),.F(dead));defparam z.INIT=2'h2;
endmodule'''
        parsed=parse(text,'test')
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'source.blif';source.write_text(parsed['blif'])
            mapped=Path(temp)/'mapped.blif'
            mapped.write_text('.model bad\n.inputs pi0 pi1\n.outputs po0\n.names pi0 pi1 po0\n01 1\n10 1\n.end\n')
            with self.assertRaises(ValueError):replace(text,parsed,mapped,source)
        with self.assertRaises(ValueError):parse('module test(a,y);input a;output y;always @* y=a;endmodule','test')

    def test_synchronous_reset_is_data_but_asynchronous_clear_is_guarded(self):
        text='''module test(a,b,clk,q,z);input a,b,clk;output q,z;wire sr,ar;
LUT1 sync(.I0(a),.F(sr));defparam sync.INIT=2'h1;
LUT1 async(.I0(b),.F(ar));defparam async.INIT=2'h1;
DFFRE ff(.D(a),.CLK(clk),.CE(1'b1),.RESET(sr),.Q(q));defparam ff.INIT=1'b0;
DFFCE cf(.D(b),.CLK(clk),.CE(1'b1),.CLEAR(ar),.Q(z));defparam cf.INIT=1'b0;
endmodule'''
        parsed=parse(text,'test')
        self.assertEqual([n['statement'].name for n in parsed['luts']],['sync'])
        self.assertEqual(parsed['protected_luts'],1)

    def test_constant_output_and_unused_input_readback(self):
        text='''module test(a,y);input a;output y;
LUT1 x(.I0(a),.F(y));defparam x.INIT=2'h0;endmodule'''
        parsed=parse(text,'test')
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'source.blif';source.write_text(parsed['blif'])
            mapped=Path(temp)/'mapped.blif'
            mapped.write_text('.model constant\n.inputs pi0\n.outputs po0\n.names po0\n.end\n')
            _,check,_=replace(text,parsed,mapped,source)
            self.assertIn('.inputs pi0\n',readback(check,parsed))
            self.assertIn('.names po0\n',readback(check,parsed))
            binary=ROOT/'build/abc/_TEST/abc.exe'
            if binary.exists():
                back=Path(temp)/'readback.blif';back.write_text(readback(check,parsed))
                self.assertTrue(cec(binary,source,back,Path(temp)/'cec.log',30)['equivalent'])


if __name__=='__main__':unittest.main()
