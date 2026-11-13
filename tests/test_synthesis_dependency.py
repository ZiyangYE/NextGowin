import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from audit_synthesis_dependency import audit


class SynthesisDependencyTest(unittest.TestCase):
    def test_saved_dependency_resolves_all_sta_imports(self):
        path = ROOT/'binary_audit/timing/synthesis_dependency.json'
        if not path.exists():
            self.skipTest('Local DLL audit not present')
        saved = json.loads(path.read_text())
        dll = Path(saved['synthesis']['path'])
        if not dll.exists():
            self.skipTest('Audited vendor installation unavailable')
        current = audit(dll.parent)
        self.assertEqual(current, saved)
        self.assertGreater(current['shared_sta']['named_exports'], 8192)
        self.assertTrue(any('?updateTiming@' in item['symbol']
                            for item in current['sta_sdc_imports']))
        manifest = json.loads((path.parent/'manifest.json').read_text())
        self.assertEqual(manifest['sha256'], current['shared_sta']['sha256'])
        self.assertEqual(manifest['pe_named_exports'], current['shared_sta']['named_exports'])
        for item in current['sta_sdc_imports']:
            self.assertEqual(manifest['exports'][item['symbol']], item['resolved_gowin_export_rva'])
        for item in manifest['evidence']:
            self.assertTrue((path.parent/item['file']).exists())


if __name__ == '__main__':
    unittest.main()
