import hashlib
import json
from pathlib import Path
import unittest
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from extract_primitive_ports import extract


class PrimitiveSourceTest(unittest.TestCase):
    def test_every_split_body_is_byte_exact(self):
        for name in ['primitive_sources_002','primitive_sources_all_002']:
            folder=ROOT/'results/timing'/name
            if not (folder/'manifest.json').exists():self.skipTest('Installed primitive split not present')
            manifest=json.loads((folder/'manifest.json').read_text())
            sources={name:Path(row['path']).read_bytes() for name,row in manifest['source'].items()}
            self.assertEqual(len({r['file'].casefold() for r in manifest['units']}),len(manifest['units']))
            for name,row in manifest['source'].items():
                self.assertEqual(hashlib.sha256(sources[name]).hexdigest(),row['sha256'])
            for row in manifest['units']:
                output=(folder/row['file']).read_bytes()
                body=sources[row['variant']][row['byte_start']:row['byte_end']]
                self.assertEqual(output[row['preamble_bytes']:-1],body)
                self.assertEqual(hashlib.sha256(output).hexdigest(),row['output_sha256'])
                self.assertIn(b'Copyright',output[:row['preamble_bytes']])

    def test_sync_and_async_controls_are_distinct_in_vendor_source(self):
        folder=ROOT/'results/timing/primitive_sources_002/tsim'
        if not folder.exists():self.skipTest('Installed primitive split not present')
        reset=(folder/'module_DFFRE.v').read_text();clear=(folder/'module_DFFCE.v').read_text()
        self.assertIn('always @(posedge CLK)',reset)
        self.assertIn('if (RESET)',reset)
        self.assertIn('always @(gsrt or CLEAR)',clear)
        self.assertIn('$setuphold (posedge CLK, posedge D, 0:0:0, 0:0:0)',reset)

    def test_primitive_directions_match_source_including_ansi_header(self):
        path=ROOT/'results/timing/primitive_ports.json'
        if not path.exists():self.skipTest('Primitive directions unavailable')
        data=json.loads(path.read_text());source=Path(data['source']).read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(),data['source_sha256'])
        self.assertEqual(extract(source.decode('utf-8')),data['ports'])
        self.assertEqual(data['ports']['DFFRE']['Q'],'output')
        self.assertEqual(data['ports']['ADCLRC']['ADCVALUE'],'output')


if __name__=='__main__':unittest.main()
