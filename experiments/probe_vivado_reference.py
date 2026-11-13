"""Read-only Vivado availability/device probe; no hardware or licensing edits."""
import ctypes,json,os,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'results/vivado_reference_probe_001';out.mkdir(exist_ok=False)
script=out/'probe.tcl';script.write_text('puts "NEXTGOWIN_VERSION [version -short]"\nputs "NEXTGOWIN_PARTS [get_parts -quiet xc7a100tcsg324-1]"\nexit\n')
ctypes.windll.kernel32.SetErrorMode(0x8003);start=time.monotonic()
cmd=['D:/Xilinx/Vivado/2022.2/bin/vivado.bat','-mode','batch','-nojournal','-nolog','-source',str(script)]
with (out/'console.log').open('w') as log:
    cp=subprocess.run(cmd,cwd=out,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW,timeout=120)
(out/'manifest.json').write_text(json.dumps(dict(returncode=cp.returncode,wall_seconds=time.monotonic()-start,scope='Version and installed part query only'),indent=2)+'\n')
print((out/'console.log').read_text(errors='replace')[-1600:])
