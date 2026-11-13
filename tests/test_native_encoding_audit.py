import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_native_encoding_probe import audit


class NativeEncodingAuditTests(unittest.TestCase):
    def test_requires_restore_and_observable_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            def write(label,bits,time):
                (p/f'encoding_{label}.fs').write_text(f'//Created Time: {time}\n{bits}\n')
            write('baseline','0000',1);write('variant','0001',2);write('restored','0000',3)
            self.assertEqual(audit(p)['status'],'reversible_difference')
            write('restored','0001',3)
            self.assertEqual(set(audit(p)['reasons']),{'baseline_not_restored','mutation_not_observable'})

    def test_rejects_unrecognized_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'encoding_baseline.fs').write_text('//Header\nNOT BITS\n')
            with self.assertRaises(ValueError):audit(temp)


if __name__=='__main__':unittest.main()
