import importlib.util
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('split',ROOT/'scripts/split_gowin_timing.py')
split = importlib.util.module_from_spec(spec)
spec.loader.exec_module(split)


class SplitTest(unittest.TestCase):
    def test_truncation_and_wrong_marker_rejected(self):
        with self.assertRaises(ValueError):
            split.Reader(b'\x01',0).read('I')
        with self.assertRaises(ValueError):
            split.Reader(b'\x01\0\0\0',0).marker(55)

    def test_installed_database_rows_match_source_bytes(self):
        source = Path('C:/Gowin/Gowin_V1.9.12.04_x64/IDE/share/device/GW5AST-138C/GW5AST-138C.tm')
        if not source.exists():
            self.skipTest('Target vendor database not installed')
        data = source.read_bytes()
        result = split.extended(data)
        self.assertEqual(result['remaining_bytes'],15)
        self.assertEqual(len(result['groups']),9)
        for group in result['groups']:
            for row in group['rows']:
                offset = row['byte_offset']
                encoded = struct.pack('<H',row['float_count'])+struct.pack('<'+str(row['float_count'])+'f',*row['values'])
                self.assertEqual(data[offset:offset+len(encoded)],encoded)
        for section in result['relations'].values():
            for table in section['tables']:
                for row in table['records']:
                    encoded = struct.pack('<HH3BH',row['source_id'],row['target_id'],*row['attributes'],len(row['values']))
                    encoded += struct.pack('<'+str(len(row['values']))+'f',*row['values'])
                    offset = row['byte_offset']
                    self.assertEqual(data[offset:offset+len(encoded)],encoded)


if __name__ == '__main__':
    unittest.main()
