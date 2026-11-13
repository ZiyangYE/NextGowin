import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from dsp_output_register_pack import pack,prove_template,validate
from gw5a_dsp import primitive_library
from yosys_runtime import run,quote
from run_abc_reproduction import sha


def fixture(folder,initialized=False):
    p=Path(folder);(p/'dsp.v').write_text(primitive_library())
    init="=1'b0" if initialized else ''
    source='module top(input clk,ce,input [26:0] a,input [17:0] b,output [5:0] q);wire [47:0] product;\n'
    source+="MULTALU27X18 #(.C_SEL(1'b0)) mult(.A(a),.B(b),.C(48'b0),.D(26'b0),.SIA(27'b0),.CASI(48'b0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b0),.CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'b0),.CLK(2'b0),.CE(2'b0),.RESET(2'b0),.DOUT(product));\n"
    for i in range(6):source+=f'reg state{i}{init};always @(posedge clk) if(ce) state{i}<=product[{i}];assign q[{i}]=state{i};\n'
    source+='endmodule\n';(p/'top.v').write_text(source)
    run(['read_verilog -lib '+quote(p/'dsp.v'),'read_verilog '+quote(p/'top.v'),'hierarchy -top top','proc','opt',
         'write_json '+quote(p/'source.json')],p,'prepare')
    return json.loads((p/'source.json').read_text())


class DSPOutputPackingTests(unittest.TestCase):
    def test_active_low_enable_requires_optin_and_its_sdk_proof(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            p=Path(temp);source=fixture(p)
            for c in source['modules']['top']['cells'].values():
                if c['type']=='$dffe':c['parameters']['EN_POLARITY']='0'
            before=copy.deepcopy(source);candidate,record=pack(source,allow_inverted_enable=True)
            self.assertEqual(source,before);self.assertEqual(record['removed_flipflops'],6)
            row=record['packed'][0];self.assertEqual(row['enable_polarity'],0)
            inv=candidate['modules']['top']['cells'][row['enable_inverter']]
            self.assertEqual(inv['type'],'$_NOT_');self.assertEqual(inv['connections'],dict(A=[row['source_enable']],Y=[row['enable']]))
            record.update(status='proved',transition_proof=prove_template(p/'pack_proof'),implementation_sha256=sha(ROOT/'scripts/dsp_output_register_pack.py'))
            record['admitted_cells']=[dict(module='top',instance='mult',cell=copy.deepcopy(candidate['modules']['top']['cells']['mult']))]
            with self.assertRaisesRegex(ValueError,'active-low enables'):validate(candidate,record,p)
            record['inverted_enable_proof']=prove_template(p/'pack_inverted_enable_proof',enable_polarity=0)
            self.assertEqual(validate(candidate,record,p),{('top','mult')})
            with self.assertRaises(RuntimeError):prove_template(p/'wrong_polarity',negative_control=True,enable_polarity=0)
            mixed=copy.deepcopy(source);next(c for c in mixed['modules']['top']['cells'].values() if c['type']=='$dffe')['parameters']['EN_POLARITY']='1'
            candidate,r=pack(mixed,allow_inverted_enable=True);self.assertEqual(candidate,mixed);self.assertEqual(r['packed'],[])

    def test_exclusive_undefined_state_bank_and_certificate_tampering(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            p=Path(temp);source=fixture(p);before=copy.deepcopy(source);candidate,record=pack(source)
            self.assertEqual(source,before);self.assertEqual(record['removed_flipflops'],6)
            self.assertEqual(len(record['packed']),1)
            proof=prove_template(p/'pack_proof')
            self.assertEqual(proof['guard_preserved_state_cells'],2)
            self.assertTrue(proof['symbolic_product_retained'])
            record.update(status='proved',transition_proof=proof,implementation_sha256=sha(ROOT/'scripts/dsp_output_register_pack.py'))
            c=candidate['modules']['top']['cells']['mult']
            record['admitted_cells']=[dict(module='top',instance='mult',cell=copy.deepcopy(c))]
            self.assertEqual(validate(candidate,record,p),{('top','mult')})
            broken=copy.deepcopy(candidate);broken['modules']['top']['cells']['mult']['connections']['CE']=['0','0']
            with self.assertRaisesRegex(ValueError,'Actual registered DSPs differ'):validate(broken,record,p)

    def test_defined_init_and_mixed_clock_or_enable_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            p=Path(temp);source=fixture(p,initialized=True);candidate,record=pack(source)
            self.assertEqual(candidate,source);self.assertEqual(record['packed'],[])
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            source=fixture(temp);flops=[c for c in source['modules']['top']['cells'].values() if c['type']=='$dffe']
            flops[0]['connections']['EN']=['0'];candidate,record=pack(source)
            self.assertEqual(candidate,source);self.assertEqual(record['packed'],[])
            source['modules']['top']['cells']['user_gsr']=dict(type='GSR',parameters={},attributes={},
                connections={'GSRI':[source['modules']['top']['ports']['ce']['bits'][0]]},port_directions={'GSRI':'input'})
            candidate,record=pack(source)
            self.assertEqual(candidate,source);self.assertEqual(record['skipped'],{'user_gsr':1})

    def test_actual_sdk_negative_enable_control_is_not_proved(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            negative=Path(temp)/'negative'
            with self.assertRaises(RuntimeError):prove_template(negative,negative_control=True)
            self.assertIn('proof did fail',(negative/'proof.console.log').read_text())

    def test_observable_product_and_preserved_state_cannot_be_absorbed(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
            original=fixture(temp)
            for mode in ('product_port','product_tap','kept_q','aliased_init','falling_clock','inverted_enable'):
                with self.subTest(mode=mode):
                    source=copy.deepcopy(original);m=source['modules']['top']
                    product=m['cells']['mult']['connections']['DOUT'][0]
                    ff=next(c for c in m['cells'].values() if c['type']=='$dffe' and c['connections']['D']==[product])
                    if mode=='product_port':
                        m['ports']['observe_product']=dict(direction='output',bits=[product])
                    elif mode=='product_tap':
                        m['cells']['tap']=dict(type='$not',parameters={'A_WIDTH':'1','Y_WIDTH':'1','A_SIGNED':'0'},
                            attributes={},port_directions={'A':'input','Y':'output'},
                            connections={'A':[product],'Y':[1000000]})
                    elif mode=='kept_q':
                        m['netnames']['preserved_alias']=dict(hide_name=0,bits=ff['connections']['Q'],attributes={'keep':'1'})
                    elif mode=='aliased_init':
                        m['netnames']['initialized_alias']=dict(hide_name=0,bits=ff['connections']['Q'],attributes={'init':'0'})
                    elif mode=='falling_clock':ff['parameters']['CLK_POLARITY']='0'
                    else:ff['parameters']['EN_POLARITY']='0'
                    candidate,record=pack(source)
                    self.assertEqual(candidate,source)
                    self.assertEqual(record['removed_flipflops'],0)


if __name__=='__main__':unittest.main()
