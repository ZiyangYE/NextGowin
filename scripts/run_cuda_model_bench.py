"""Use exported physical-pin delay bounds in the native scoring microbenchmark."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--binary',type=Path,default=Path('build/cuda/cut_bench.exe'))
    args = ap.parse_args()
    model = json.loads(args.model.read_text())
    pins = [math.ceil(pin['setup_max_ns']*1000) for pin in model['physical_lut_inputs']]
    cp = subprocess.run([str(args.binary.resolve()),*map(str,pins)],capture_output=True,text=True,check=True,timeout=60)
    result = json.loads(cp.stdout)
    if result['pin_delays_ps']!=pins or not all(r['correct'] for r in result['runs']):
        raise RuntimeError('Wrong model or CPU/GPU mismatch')
    result['model_sha256'] = hashlib.sha256(args.model.read_bytes()).hexdigest()
    result['binary_sha256'] = hashlib.sha256(args.binary.read_bytes()).hexdigest()
    result['model_scope'] = model['scope']
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
