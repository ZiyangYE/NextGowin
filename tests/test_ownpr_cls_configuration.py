import sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_cls_configuration import ClsChoices,audit_sweep


@unittest.skipUnless((ROOT/'results/native_cls_modes_002/cls_mode_sweep.json').exists(),'Requires native CLS capture')
class NativeClsEncodingTests(unittest.TestCase):
    def test_native_normalized_attribute_differences(self):
        result=audit_sweep(ROOT/'results/native_cls_modes_002')
        self.assertEqual(result['status'],'verified_sample_cls_predicates')
        self.assertFalse(result['primitive_attribute_semantics_verified'])
        self.assertLess(result['selected_row_coverage'],result['total_rows'])

    def test_wrong_negative_predicate_rejected(self):
        original=ClsChoices.selected
        def wrong(self,predicate,attributes):
            value=original(self,predicate,attributes)
            return not value if predicate<0 else value
        with patch.object(ClsChoices,'selected',wrong):
            self.assertEqual(audit_sweep(ROOT/'results/native_cls_modes_002')['status'],'failed')


if __name__=='__main__':unittest.main()
