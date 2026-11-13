"""Shape/batch study of exact CPU/CUDA timing on actual mapped logic graphs."""
import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys
from run_abc_reproduction import ROOT,sha


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--context',default='CPU/GPU exclusivity not established; diagnostic performance only')
    args=ap.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    cases=[('ctrl',ROOT/'results/abc_paper_001/ctrl/baseline_0.blif',1),
           ('log2',ROOT/'results/abc_paper_001/log2/acd_0.blif',1),
           ('sqrt',ROOT/'results/abc_paper_001/sqrt/acd_remap_0.blif',1),
           ('core',ROOT/'results/fpgafly_physical_copies_002/source.blif',521)]
    result=dict(scope='Fixed-DAG timing propagation on mapped graphs, not complete mapper or RTL-flow acceleration',
        context=args.context,status='running',runs=[])
    def save():(out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    save()
    for name,path,delay in cases:
        for batch in [1,64]:
            folder=out/(name+'_'+str(batch));folder.mkdir()
            commands=[
                [sys.executable,str(ROOT/'scripts/prepare_timing_graph.py'),str(path),'--configs',str(batch),'--delay-ps',str(delay),'--out',str(folder/'prepared')],
                [sys.executable,str(ROOT/'scripts/run_timing_bench.py'),str(folder/'prepared'),'--context',args.context,'--out',str(folder/'measurement')]]
            for stage,command in zip(['prepare','measure'],commands):
                with (folder/(stage+'.log')).open('w') as log:
                    cp=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=240)
                if cp.returncode:raise RuntimeError(f'{name}/{batch}/{stage} failed; inspect log')
            measurement=json.loads((folder/'measurement/manifest.json').read_text())
            row=dict(name=name,batch=batch,input_sha256=sha(path),nodes=measurement['nodes'],levels=measurement['levels'],
                gpu_correct=measurement['gpu_correct'],cpu_correct=measurement['cpu_correct'],
                cpu_threads=measurement['cpu_threads'],**measurement['medians'],cold_gpu_call_ms=measurement['cold_gpu_call_ms'],
                application_device_buffer_bytes=measurement['application_device_buffer_bytes'],
                measurement_manifest_sha256=sha(folder/'measurement/manifest.json'))
            result['runs'].append(row);save();print(json.dumps(row),flush=True)
    result['status']='complete' if all(r['gpu_correct'] for r in result['runs']) else 'complete_with_cpu_fallbacks';save()
    fields=['name','batch','nodes','levels','cpu_threads','cpu_serial_ms','cpu_parallel_ms','gpu_resident_wall_ms',
            'gpu_with_seed_transfer_and_readback_ms','cold_gpu_call_ms','application_device_buffer_bytes','gpu_correct']
    with (out/'summary.csv').open('w',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(result['runs'])


if __name__=='__main__':main()
