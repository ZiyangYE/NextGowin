from pathlib import Path
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from run_abc_reproduction import ABC_SOURCE,network_sha


class MuCacheTest(unittest.TestCase):
    def setUp(self):
        self.binary=ROOT/'build/abc_mu/_TEST/abc.exe'
        self.source=ROOT/'results/abc_paper_001/adder/normalized.aig'
        if not self.binary.exists() or not self.source.exists():
            self.skipTest('Build the experimental MU mapper and adder corpus first')

    def run_mapper(self,folder,cache,verify=False):
        env=dict(os.environ)
        for key in ['NEXTGOWIN_MU_CACHE','NEXTGOWIN_MU_STATS','NEXTGOWIN_MU_VERIFY']:
            env.pop(key,None)
        env['NEXTGOWIN_MU_CACHE']=str(cache)
        env['NEXTGOWIN_MU_STATS']=str(folder/'stats.json')
        if verify:env['NEXTGOWIN_MU_VERIFY']='1'
        target=folder/'mapped.blif'
        command=f'read "{self.source.as_posix()}"; dch; if -Z 6 -K 8; write_blif "{target.as_posix()}"'
        return subprocess.run([str(self.binary),'-c',command],cwd=ABC_SOURCE,
                              env=env,capture_output=True,text=True,timeout=30)

    def test_empty_table_falls_back_and_preserves_mapping(self):
        reference=ROOT/'results/mu_mapper_adder_001/ordinary/mapped_0.blif'
        if not reference.exists():self.skipTest('Reference experiment absent')
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);cache=folder/'cache.bin'
            cache.write_bytes(b'ACDMU001'+struct.pack('<I',0))
            cp=self.run_mapper(folder,cache)
            self.assertEqual(cp.returncode,0,cp.stdout+cp.stderr)
            stat=json.loads((folder/'stats.json').read_text())
            self.assertGreater(stat['misses'],0)
            self.assertEqual(stat['hits'],0)
            self.assertEqual(network_sha(folder/'mapped.blif'),network_sha(reference))

    def test_truncated_table_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);cache=folder/'cache.bin'
            cache.write_bytes(b'ACDMU001'+struct.pack('<I',1))
            cp=self.run_mapper(folder,cache)
            self.assertEqual(cp.returncode,17)
            self.assertIn('NEXTGOWIN_MU_ERROR',cp.stderr)

    def test_verifier_detects_wrong_valid_range_multiplicity(self):
        source=ROOT/'results/mu_mapper_adder_001/mu_cache.bin'
        if not source.exists():self.skipTest('Validated GPU cache absent')
        data=bytearray(source.read_bytes());count=struct.unpack_from('<I',data,8)[0];pos=12
        for _ in range(count):
            n,k,words=struct.unpack_from('<3I',data,pos);pos+=12+8*words
            for i in range(1<<n):
                if data[pos+i]!=255:data[pos+i]=data[pos+i]%64+1
            pos+=1<<n
        self.assertEqual(pos,len(data))
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);cache=folder/'cache.bin';cache.write_bytes(data)
            cp=self.run_mapper(folder,cache,verify=True)
            self.assertEqual(cp.returncode,17)
            self.assertIn('GPU/CPU multiplicity mismatch',cp.stderr)


if __name__=='__main__':unittest.main()
