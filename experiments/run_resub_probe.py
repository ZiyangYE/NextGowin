"""Measure bounded, level-preserving LUT resubstitution before physical tests."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from run_abc_reproduction import invoke,cec

source=Path(sys.argv[1]).resolve()
out=Path(sys.argv[2]).resolve();out.mkdir(parents=True)
target=out/'mapped.blif'
command=f'read "{source.as_posix()}"; print_stats; mfs2 -W 2 -F 30 -D 0 -M 300 -C 1000; print_stats; write_blif "{target.as_posix()}"; echo NEXTGOWIN_DONE'
result,log=invoke(ROOT/'build/abc/_TEST/abc.exe',command,out/'mapping.log',180)
print(result);print(log[-1500:])
if result['status']!='ok':raise SystemExit(1)
print(cec(ROOT/'build/abc/_TEST/abc.exe',source,target,out/'cec.log',180))
