"""Build the native timing benchmark and bind its binary to source hashes."""
import json
from pathlib import Path
import subprocess
from run_abc_reproduction import ROOT,sha


def main():
    files=[ROOT/'cuda/timing_bench.cu',ROOT/'scripts/build_timing_cuda.cmd']
    before={str(p.relative_to(ROOT)):sha(p) for p in files}
    output=ROOT/'build/cuda';output.mkdir(parents=True,exist_ok=True)
    with (output/'timing_build.log').open('w') as log:
        cp=subprocess.run(['cmd.exe','/c',str(files[1])],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
    if cp.returncode:raise RuntimeError('Timing CUDA build failed; see build/cuda/timing_build.log')
    if before!={str(p.relative_to(ROOT)):sha(p) for p in files}:raise RuntimeError('Build sources changed during compilation')
    binary=output/'timing_bench.exe'
    (output/'timing_build_manifest.json').write_text(json.dumps(dict(sources=before,binary_sha256=sha(binary)),indent=2)+'\n')
    print(binary)


if __name__=='__main__':main()
