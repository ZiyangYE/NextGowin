from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from flatten_gowin_core import flatten,emitted_graph
from gowin_cones import literal


SOURCE='''module child(x,y);
input [0:1] x;output [1:0] y;
LUT2 parity(.I0(x[0]),.I1(x[1]),.F(y[0]));defparam parity.INIT=4'h6;
LUT1 inverse(.I0(x[0]),.F(y[1]));defparam inverse.INIT=2'h1;
endmodule
module core(a,q);
input [3:0] a;output [3:0] q;wire [1:0] t;
child u(.x({a[0],a[2]}),.y(t));
assign q[0]=t[0];assign q[1]=t[1];assign q[3:2]={2{a[3]}};
endmodule
module top(a,q);input [3:0] a;output [3:0] q;core c(.a(a),.q(q));endmodule
'''


class FlattenTest(unittest.TestCase):
    def test_ascending_formals_concat_repeat_and_public_aliases(self):
        text,metadata,mapping=flatten(SOURCE,'core','top')
        self.assertEqual(metadata['original_leaf_count'],2)
        self.assertEqual(metadata['remaining_module_count'],2)
        self.assertEqual(metadata['pruned_modules'],['child'])
        parsed,uf,cells=emitted_graph(text,'core')
        for value in range(16):
            values={uf.find('a['+str(i)+']'):(value>>i)&1 for i in range(4)}
            pending=dict(cells)
            while pending:
                progress=False
                for name,cell in list(pending.items()):
                    k=int(cell['kind'][-1]);inputs=[cell['pins']['I'+str(i)][0] for i in range(k)]
                    if any(p not in values for p in inputs):continue
                    _,truth=literal(cell['parameters']['INIT'])
                    index=sum(values[p]<<i for i,p in enumerate(inputs))
                    values[cell['pins']['F'][0]]=(truth>>index)&1;pending.pop(name);progress=True
                self.assertTrue(progress)
            output=[values[uf.find('q['+str(i)+']')] for i in range(4)]
            self.assertEqual(output,[(value&1)^((value>>2)&1),1-(value&1),(value>>3)&1,(value>>3)&1])

    def test_state_instances_remain_distinct_with_same_parameters(self):
        text='''module child(d,clk,q);input d,clk;output q;
DFFRE ff(.D(d),.CLK(clk),.CE(1'b1),.RESET(1'b0),.Q(q));defparam ff.INIT=1'b1;endmodule
module core(d,clk,q);input [1:0] d;input clk;output [1:0] q;
child a(.d(d[0]),.clk(clk),.q(q[0]));child b(.d(d[1]),.clk(clk),.q(q[1]));endmodule
module top(d,clk,q);input [1:0] d;input clk;output [1:0] q;core c(.d(d),.clk(clk),.q(q));endmodule'''
        candidate,metadata,mapping=flatten(text,'core','top')
        self.assertEqual([m['original_path'] for m in mapping],[['a','ff'],['b','ff']])
        self.assertEqual(metadata['leaf_types']['DFFRE'],2)
        _,_,cells=emitted_graph(candidate,'core')
        flops=[cell for cell in cells.values() if cell['kind']=='DFFRE']
        self.assertEqual([c['parameters']['INIT'] for c in flops],["1'b1"]*2)
        self.assertNotEqual(flops[0]['pins']['D'],flops[1]['pins']['D'])

    def test_width_mismatch_and_inout_are_rejected(self):
        with self.assertRaises(ValueError):flatten(SOURCE.replace('.x({a[0],a[2]})','.x(a[0])'),'core','top')
        with self.assertRaises(ValueError):flatten(SOURCE.replace('input [3:0] a;output [3:0] q;wire','inout [3:0] a;output [3:0] q;wire'),'core','top')

    def test_multiple_independent_wire_drivers_are_rejected(self):
        text='''module core(a,b,q);input a,b;output q;
LUT1 a0(.I0(a),.F(q));defparam a0.INIT=2'h2;
LUT1 b0(.I0(b),.F(q));defparam b0.INIT=2'h2;endmodule
module top(a,b,q);input a,b;output q;core c(.a(a),.b(b),.q(q));endmodule'''
        with self.assertRaises(ValueError):flatten(text,'core','top')


if __name__=='__main__':unittest.main()
