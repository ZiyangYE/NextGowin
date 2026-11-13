import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from gw5a_box_mapper import map_design, scoped_candidate
from insert_gowin_pads import insert
from yosys_runtime import run, quote


class BoxMappingTests(unittest.TestCase):
    def test_scope_restores_opaque_bidirectional_interfaces(self):
        original = dict(modules={
            'top': dict(attributes={'top': '1'}, cells={}, ports={}),
            'ddr': dict(cells={'pad': dict(type='IOBUF', connections={'IO': [2], 'O': [3]})},
                        ports={'io': dict(direction='inout', bits=[2])})})
        mapped = copy.deepcopy(original)
        mapped['modules']['ddr']['cells']['pad']['connections']['IO'] = [3]
        mapped['modules']['top']['attributes'] = {}
        reference, actual, zeros = scoped_candidate(original, mapped, ['top'])
        self.assertEqual(reference, original)
        self.assertEqual(actual, original)
        self.assertEqual(zeros, [0, 0])

    def test_box_mapping_preserves_feedback_state_and_real_io(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            p = Path(temp)
            rtl = p/'top.v'
            rtl.write_text('''module top(input clk,ce,rst,a,b,inout io,output q);
wire state, pad_data;
wire next_state = ((a & b) | (state & ~a)) ^ pad_data;
DFFRE ff(.D(next_state),.Q(state),.CLK(clk),.CE(ce),.RESET(rst));
IOBUF pad(.I(state),.OEN(1'b1),.O(pad_data),.IO(io));
assign q=state;
endmodule
''')
            run(['read_verilog -lib +/gowin/cells_sim.v', 'read_verilog '+quote(rtl),
                 'hierarchy -check -top top', 'proc', 'techmap', 'opt -fast',
                 'blackbox =A:whitebox', 'write_json '+quote(p/'source.json')], p, 'prepare')
            vendor = p/'vendor.v'
            vendor.write_text('')
            result = map_design(p/'source.json', p/'map', vendor, period_ps=10000)
            self.assertEqual(result['status'], 'complete')
            self.assertFalse(result['vendor_synthesis_seed'])
            self.assertTrue(all(row['cec']['equivalent'] for row in result['native_proofs']))
            before = json.loads((p/'source.json').read_text())
            after = json.loads((p/'map/mapped.json').read_text())
            for name in set(before['modules'])-{'top'}:
                self.assertEqual(before['modules'][name], after['modules'][name])
            after, _ = insert(after, 'top')
            m = after['modules']['top']
            self.assertEqual(m['cells']['pad']['connections']['IO'], m['ports']['io']['bits'])


if __name__ == '__main__':
    unittest.main()
