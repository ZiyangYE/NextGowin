"""Observe extended I/O after one native encoding of an exact own-P&R import."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import native_pin_feedback_probe as base
from native_io_extended_state import capture


def worker(out,placement,control=False):
    if control:raise ValueError('Extended IO probe does not support control mode')
    source=(out/'adapter_source.py.txt').read_text()
    marker="        (out/'io_state_after_encoding.json').write_text(json.dumps(capture(native),indent=2))"
    if source.count(marker)!=1:raise ValueError('Native adapter encoding hook changed')
    source=source.replace(marker,marker+"\n        capture_extended_io(native,out)")
    def observe(native,folder):
        result=capture(native);result['encoding_phase']='after_fresh_process_single_encoding'
        (folder/'pin_feedback.json').write_text(json.dumps(result,indent=2))
    namespace={'__name__':'isolated_extended_io_import','__file__':str(ROOT/'experiments/native_import_ownpr.py'),'capture_extended_io':observe}
    exec(compile(source,str(out/'adapter_source.py.txt'),'exec'),namespace)
    namespace['worker'](out,placement,True,True,False,True)


if __name__=='__main__':
    base.__file__=__file__;base.worker=worker;base.main()
    if '--worker' not in sys.argv:
        from ownpr_image_storage import sha
        out=Path(sys.argv[sys.argv.index('--out')+1]).resolve();m=json.loads((out/'manifest.json').read_text())
        m.update(bitstream_generated=(out/'probe.fs').is_file(),observation='extended_io_after_single_encoding',
            implementation_sha256=sha(ROOT/'scripts/native_io_extended_state.py'),extended_capture_sha256=sha(out/'pin_feedback.json'))
        if m['bitstream_generated']:m['image_sha256']=sha(out/'probe.fs')
        (out/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps(dict(status=m['status'],bitstream_generated=m['bitstream_generated'])))
