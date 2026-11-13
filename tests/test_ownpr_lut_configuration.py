import sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_lut_configuration import LutInitChoices,audit_sweep


@unittest.skipUnless((ROOT/'results/native_lut_init_sweep_001/lut_init_sweep.json').exists(),'Requires native calibration capture')
class NativeLutEncodingTests(unittest.TestCase):
    def test_native_bit_patterns_match_and_restore(self):
        result=audit_sweep(ROOT/'results/native_lut_init_sweep_001')
        self.assertEqual(result['status'],'verified_sample_lut_init_mapping')
        self.assertEqual(result['data_bits_covered'],1360)
        self.assertFalse(result['full_bitstream_verified'])

    @unittest.skipUnless((ROOT/'results/native_device_lut_004/lut_init_sweep.json').exists(),'Requires device-only native capture')
    def test_all_native_cfu_tile_types(self):
        result=audit_sweep(ROOT/'results/native_device_lut_004')
        self.assertEqual(result['status'],'verified_sample_lut_init_mapping')
        self.assertEqual(result['tile_types'],[17,221,222,223,224,225,226,227,228])
        self.assertEqual(result['data_bits_covered'],1152)

    def test_wrong_truth_bit_order_is_rejected(self):
        original=LutInitChoices.template
        def wrong(self,typ):
            return {(slot,bit^1):value for (slot,bit),value in original(self,typ).items()}
        with patch.object(LutInitChoices,'template',wrong):
            try:result=audit_sweep(ROOT/'results/native_lut_init_sweep_001')
            except ValueError as error:self.assertIn('baseline disagrees',str(error))
            else:self.assertEqual(result['status'],'failed')


if __name__=='__main__':unittest.main()
