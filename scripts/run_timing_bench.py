"""Run an exact timing-DAG benchmark with source/binary/input provenance."""
import argparse
import json
from pathlib import Path
import statistics
import subprocess
import time
from run_abc_reproduction import ROOT,sha


def telemetry():
    try:
        command=['nvidia-smi','--query-gpu=utilization.gpu,memory.used,memory.free,temperature.gpu,power.draw','--format=csv,noheader,nounits']
        cp=subprocess.run(command,capture_output=True,text=True,timeout=10)
        return dict(fields=['utilization_percent','memory_used_mib','memory_free_mib','temperature_c','power_w'],
            raw_rows=cp.stdout.strip().splitlines(),returncode=cp.returncode)
    except (OSError,subprocess.TimeoutExpired):return dict(status='unavailable')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('prepared',type=Path)
    ap.add_argument('--cpu-only',action='store_true');ap.add_argument('--threads',type=int,default=8)
    ap.add_argument('--context',default='Host/GPU exclusivity not established')
    ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    prepared=json.loads((args.prepared/'manifest.json').read_text());graph=args.prepared/'graph.bin'
    if sha(graph)!=prepared['graph_sha256']:raise ValueError('Timing graph artifact changed')
    binary=ROOT/'build/cuda/timing_bench.exe';build=json.loads((ROOT/'build/cuda/timing_build_manifest.json').read_text())
    if sha(binary)!=build['binary_sha256'] or any(sha(ROOT/path)!=digest for path,digest in build['sources'].items()):
        raise ValueError('Timing executable/build sources changed; rebuild first')
    command=[str(binary),str(graph.resolve()),'--threads',str(args.threads)]+(['--cpu-only'] if args.cpu_only else [])
    before=telemetry()
    started=time.perf_counter();cp=subprocess.run(command,capture_output=True,text=True,timeout=180)
    wall=time.perf_counter()-started
    after=telemetry()
    (out/'stdout.log').write_text(cp.stdout);(out/'stderr.log').write_text(cp.stderr)
    if cp.returncode:raise RuntimeError(f'Timing validation failed ({cp.returncode}); inspect logs')
    result=json.loads(cp.stdout)
    if not result['cpu_correct'] or (result['gpu_correct'] and result['mutable_boundary_checks']!=2):
        raise RuntimeError('Incomplete exact timing validation')
    medians={key:statistics.median(result[key]) for key in ['cpu_serial_ms','cpu_parallel_ms','gpu_resident_wall_ms',
        'gpu_event_ms','gpu_with_seed_transfer_and_readback_ms'] if result[key] and (key.startswith('cpu_') or result['gpu_correct'])}
    result.update(medians=medians,host_activity_context=args.context,graph_sha256=sha(graph),binary_sha256=sha(binary),
        prepared_manifest_sha256=sha(args.prepared/'manifest.json'),build_manifest=build,
        process_wall_seconds=wall,
        scope_limits='Fixed DAG only. GPU cold call excludes file/Python preparation and CPU validation; process wall includes repeated correctness/performance checks. Not mapper end-to-end acceleration.',
        memory_scope='Application buffer bytes plus device free-memory snapshots; not a sampled process peak-memory measurement')
    result['gpu_telemetry_before']=before;result['gpu_telemetry_after']=after
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(nodes=result['nodes'],levels=result['levels'],snapshots=result['snapshots'],
        gpu_correct=result['gpu_correct'],medians=medians,process_wall_seconds=wall),indent=2))


if __name__=='__main__':main()
