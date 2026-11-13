import subprocess,sys
from pathlib import Path
import psutil
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from run_ownpr_pin_closure import stop_owned_process_tree


def test_budget_stop_reaps_the_native_style_child_worker():
    code="import subprocess,sys,time;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print(p.pid,flush=True);time.sleep(60)"
    process=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        pid=int(process.stdout.readline().strip());worker=psutil.Process(pid)
        assert worker.is_running()
        stop_owned_process_tree(process)
        assert process.poll() is not None and not worker.is_running()
    finally:
        if process.poll() is None:stop_owned_process_tree(process)
        process.stdout.close();process.stderr.close()


def test_completed_stage_is_not_treated_as_a_live_worker():
    process=subprocess.Popen([sys.executable,'-c','pass'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));process.wait(timeout=10)
    stop_owned_process_tree(process)
    assert process.returncode==0
