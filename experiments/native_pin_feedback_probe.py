"""Isolated observation of native pin feedback after importing OWN routed state.

No native P&R and no encoding. Query meanings must be established by comparing
the captured values with complete final STA, never inferred from export names.
"""
import argparse,json,os,shutil,subprocess,sys,time,hashlib,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import sha,copy_original
from workspace_budget import reserve

def capture(native,out,logical_pins,pin_label,mapping,control=False):
    import ctypes as C
    vp=C.c_void_p;fn=native.fn;monitor=native.monitor
    cached=fn('?getSinkSlack@TmMonitor@STA@@QEAAMPEAVPin@NETLIST@@@Z',C.c_float,[vp,vp])
    selected=fn('?getSinkSlack@TmMonitor@STA@@QEAAMPEAVPin@NETLIST@@_N@Z',C.c_float,[vp,vp,C.c_bool])
    arrival=fn('?getArrivalTimeForSyn@TmMonitor@STA@@QEAAMPEAVPin@NETLIST@@_N1@Z',C.c_float,[vp,vp,C.c_bool,C.c_bool])
    def value(v):return v if math.isfinite(v) else str(v)
    pins=[];seen=set()
    for name,direction,pin,net in ([] if control else logical_pins):
        if pin in seen:continue
        seen.add(pin);pins.append((pin,dict(instance=name,native_name=mapping['instances'][name],pin=pin_label(pin),direction=direction,has_vertex=bool(native.ptr(pin+0x20)),cached_before=value(cached(monitor,pin)))))
    for mode in (False,True):
        for pin,row in pins:
            row['selected_'+str(int(mode))]=value(selected(monitor,pin,mode))
            row['cached_after_'+str(int(mode))]=value(cached(monitor,pin))
            row['arrival_after_'+str(int(mode))]={str(int(a))+str(int(b)):value(arrival(monitor,pin,a,b)) for a in (False,True) for b in (False,True)}
    shutil.copyfile(out/'probe.tr',out/'report_before.tr')
    rc=fn('?generateTimingRptoutFile@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])(native.mem)
    shutil.copyfile(out/'probe.tr',out/'report_after.tr')
    # A report may update cached pin state; capture it separately.
    for pin,row in pins:row['cached_after_report']=value(cached(monitor,pin))
    result=dict(status='captured',pins=[row for _,row in pins],report_returncode=rc,
        scope='Unvalidated API observations. Boolean meanings and sentinel validity require report cross-check.',
        control_without_queries=control,vendor_placement_executed=False,vendor_routing_executed=False,used_for_optimization=False)
    (out/'pin_feedback.json').write_text(json.dumps(result,separators=(',',':'))+'\n')
    print('PIN_FEEDBACK_CAPTURED',len(pins),flush=True)

def worker(out,placement,control=False):
    source=(out/'adapter_source.py.txt').read_text();marker="            result['sta_report_generated']=(out/'probe.tr').exists()"
    assert source.count(marker)==1
    source=source.replace(marker,marker+'\n            capture_pin_feedback(native,out,logical_pins,pin_label,mapping)\n            return')
    namespace={'__name__':'isolated_native_import','__file__':str(ROOT/'experiments/native_import_ownpr.py'),'capture_pin_feedback':lambda *args:capture(*args,control=control)}
    exec(compile(source,str(out/'adapter_source.py.txt'),'exec'),namespace)
    namespace['worker'](out,placement,True,True,False,True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path);p.add_argument('--placement',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');p.add_argument('--control',action='store_true');a=p.parse_args()
    out=a.out.resolve();placement=a.placement.resolve()
    if a.worker:return worker(out,placement,a.control)
    reserve(ROOT,600_000_000);start=time.monotonic();source=a.source.resolve();out.mkdir(exist_ok=False)
    meta=json.loads((source/'manifest.json').read_text())
    assert meta['status']=='imported' and meta['sta_completed'] and not meta['vendor_placement_executed'] and not meta['vendor_routing_executed']
    assert meta['packed_sha256']==sha(placement/'packed.json') and meta['placement_sha256']==sha(placement/'placement.json')
    names=('probe.vg','name_map.json','project.cst','probe.sdc','clock_tree.json','expanded_routes.json','partial_configuration.json','io_reference.json')
    hashes={n:sha(source/n) for n in names}
    for n in names:copy_original(source/n,out/n)
    (out/'cmd.do').write_text((source/'cmd.do').read_text().replace(source.as_posix(),out.as_posix()))
    shutil.copyfile(ROOT/'experiments/native_import_ownpr.py',out/'adapter_source.py.txt');shutil.copyfile(__file__,out/'probe_source.py.txt')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';expired=False;rc=None
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out),'--placement',str(placement)]+(['--control'] if a.control else []),cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=420,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).returncode
        except subprocess.TimeoutExpired:expired=True
    result=dict(status='captured' if rc==0 and (out/'pin_feedback.json').exists() else 'failed',returncode=rc,timeout=expired,wall_seconds=time.monotonic()-start,
        source=str(source),placement=str(placement),input_sha256=hashes,adapter_sha256=sha(out/'adapter_source.py.txt'),probe_sha256=sha(out/'probe_source.py.txt'),
        control_without_queries=a.control,vendor_placement_executed=False,vendor_routing_executed=False,bitstream_generated=False,used_for_optimization=False)
    if (out/'pin_feedback.json').exists():result['capture_sha256']=sha(out/'pin_feedback.json')
    (out/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    if result['status']!='captured':raise SystemExit(1)

if __name__=='__main__':main()
