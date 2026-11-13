import json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_device import Device
from ownpr_clock_graph import ClockGraph
from ownpr_clock_configuration import ClockChoices
from audit_native_clock_encoding import audit


class NativeClockConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.device=Device();cls.graph=ClockGraph(ROOT/'results/native_clock_inventory_005')

    def test_entire_clock_delta_and_mask_values_match(self):
        result=audit(ROOT/'results/native_clock_encoding_001',choices=ClockChoices(self.device,self.graph))
        self.assertEqual(result['status'],'clock_candidate_bits_matched')
        self.assertEqual((result['predicted_changes'],result['actual_changes'],result['required_bits']),(62,62,225))
        self.assertTrue(result['full_payload_restored']);self.assertFalse(result['full_clock_legality_verified'])

    def test_corrupt_cmux_bit_mapping_is_rejected(self):
        choices=ClockChoices(self.device,self.graph);original=choices.ordinary.bit
        def wrong(typ,index):
            row,column=original(typ,index)
            return row,column^1 if typ==208 else column
        choices.ordinary.bit=wrong
        result=audit(ROOT/'results/native_clock_encoding_001',choices=choices)
        self.assertEqual(result['status'],'failed');self.assertGreater(result['missing']+result['unexpected'],0)

    def test_uart_primary_aliases_match_native_encoding(self):
        result=audit(ROOT/'results/native_uart_clock_encoding_001',ROOT/'results/ownpr_uart_clock_001/clock_tree.json',choices=ClockChoices(self.device,self.graph))
        self.assertEqual(result['status'],'clock_candidate_bits_matched')
        self.assertEqual((result['predicted_changes'],result['actual_changes'],result['required_bits']),(226,226,874))
        self.assertTrue(result['full_payload_restored'])
        self.assertFalse(result['full_clock_legality_verified'])

    def test_uncalibrated_primary_alias_selector_is_rejected(self):
        tree=json.loads((ROOT/'results/ownpr_uart_clock_001/clock_tree.json').read_text())
        alias=next(r for r in tree['parents'] if r['node']==[-2,-1,297])
        alias['parent']=[-2,-1,237] # PCLKTOP0 -> P30A, not calibrated P30 -> P30A
        with self.assertRaisesRegex(ValueError,'physical configuration site|Unsupported clock span alias'):
            ClockChoices(self.device,self.graph).requirements(tree)


if __name__=='__main__':unittest.main()
