from pathlib import Path
import json
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'experiments'))
from run_full_abc9 import native_interfaces
from yosys_runtime import run,quote


class NativeInterfaceRoundtripTests(unittest.TestCase):
    def test_pad_alias_and_dsp_parameter_survive_buffer_normalization(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            p=Path(temp);library=native_interfaces(p/'library.v',{'IOBUF','MULTALU27X18'})
            rtl=p/'top.v';rtl.write_text('''module top(input d,oe,inout io,output q,output [47:0] product);
IOBUF pad(.I(d),.OEN(oe),.O(q),.IO(io));
MULTALU27X18 #(.C_SEL(1'b0)) dsp(.A(27'b0),.B(18'b0),.C(48'b0),.D(26'b0),.SIA(27'b0),.CASI(48'b0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b0),.CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'b0),.CLK(2'b0),.CE(2'b0),.RESET(2'b0),.DOUT(product));
endmodule
''')
            run(['read_verilog -lib '+quote(library),'read_verilog '+quote(rtl),'hierarchy -check -top top',
                 'bufnorm -conn','write_json '+quote(p/'design.json')],p,'roundtrip')
            m=json.loads((p/'design.json').read_text())['modules']['top']
            self.assertEqual(m['cells']['pad']['connections']['IO'],m['ports']['io']['bits'])
            self.assertNotEqual(m['cells']['pad']['connections']['O'],m['ports']['io']['bits'])
            self.assertEqual(int(m['cells']['dsp']['parameters']['C_SEL'],2),0)


if __name__=='__main__':unittest.main()
