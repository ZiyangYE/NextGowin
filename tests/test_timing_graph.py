import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from prepare_timing_graph import build,write,INF
from gowin_blif import read_blif
from run_abc_reproduction import sha


TEXT='''.model fork
.inputs a b unused
.outputs q r c
.names a b n
11 1
.names n a q
1- 1
-1 1
.names n r
0 1
.names c
1
.names unused dead
1 1
.end
'''


class TimingGraphTest(unittest.TestCase):
    def make(self,folder,text=TEXT,boundaries=None):
        path=folder/'source.blif';path.write_text(text)
        return build(read_blif(path,max_inputs=6),1,521,boundaries=boundaries)

    def test_reconvergent_multi_endpoint_and_unconstrained_nodes(self):
        with tempfile.TemporaryDirectory() as temp:
            data=self.make(Path(temp),boundaries=({'a':5000,'b':1000,'unused':0},{'q':6500,'r':7000,'c':6400}))
            names={n:i for i,n in enumerate(data['names'])};arr,req=data['arrays'][-2:]
            self.assertEqual(arr[0,names['q']],6042)
            self.assertEqual(arr[0,names['r']],6042)
            self.assertEqual(arr[0,names['c']],0)
            self.assertEqual(req[0,names['a']],5458)
            self.assertEqual(req[0,names['unused']],INF)
            self.assertEqual(req[0,names['dead']],INF)

    def test_deep_narrow_graph_and_constant_only_graph(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);lines=['.model chain','.inputs a','.outputs n1499']
            for i in range(1500):lines.extend([f'.names {"a" if i==0 else "n"+str(i-1)} n{i}','1 1'])
            lines.append('.end')
            data=self.make(folder,'\n'.join(lines)+'\n',({'a':0},{'n1499':1000000}))
            self.assertEqual(data['header'][2],1501)
            self.assertEqual(data['arrays'][-2][0,-1],781500)
            self.assertEqual(data['arrays'][-1][0,0],218500)
            constant=self.make(folder,'.model c\n.inputs a\n.outputs q\n.names q\n1\n.end\n')
            self.assertEqual(constant['header'][1],0)

    def test_native_cpu_oracle_corruption_and_truncation(self):
        binary=ROOT/'build/cuda/timing_bench.exe';manifest=ROOT/'build/cuda/timing_build_manifest.json'
        if not binary.exists() or not manifest.exists():self.skipTest('Native timing benchmark not built')
        record=json.loads(manifest.read_text())
        if sha(binary)!=record['binary_sha256'] or any(sha(ROOT/p)!=s for p,s in record['sources'].items()):self.skipTest('Rebuild timing benchmark after source changes')
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);data=self.make(folder);path=folder/'graph.bin';write(path,data)
            def run():return subprocess.run([str(binary),str(path),'--cpu-only'],capture_output=True,text=True,timeout=30)
            good=run();self.assertEqual(good.returncode,0,good.stderr)
            self.assertTrue(json.loads(good.stdout)['cpu_correct'])
            unavailable=subprocess.run([str(binary),str(path)],env=dict(os.environ,CUDA_VISIBLE_DEVICES='-1'),
                capture_output=True,text=True,timeout=30)
            self.assertEqual(unavailable.returncode,0,unavailable.stderr)
            fallback=json.loads(unavailable.stdout)
            self.assertTrue(fallback['cpu_correct']);self.assertFalse(fallback['gpu_correct'])
            self.assertTrue(fallback['cpu_fallback_reason'])
            data['arrays'][-2][0,0]+=1;write(path,data)
            bad=run();self.assertEqual(bad.returncode,17);self.assertIn('TIMING_MISMATCH',bad.stderr)
            path.write_bytes(path.read_bytes()[:-8]);truncated=run()
            self.assertEqual(truncated.returncode,3);self.assertIn('truncated timing graph',truncated.stderr)


if __name__=='__main__':unittest.main()
