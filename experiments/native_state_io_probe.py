"""One exact import: observe timing pairs before encoding and full IO afterward."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import native_pin_feedback_probe as base
from native_timing_state_probe import capture as capture_time
from native_io_extended_state import capture as capture_io


def worker(out,placement,control=False):
    if control:raise ValueError('Combined capture does not support control mode')
    source=(out/'adapter_source.py.txt').read_text()
    sta="            result['sta_report_generated']=(out/'probe.tr').exists()"
    encoded="        (out/'io_state_after_encoding.json').write_text(json.dumps(capture(native),indent=2))"
    if source.count(sta)!=1 or source.count(encoded)!=1:raise ValueError('Native observation hooks changed')
    source=source.replace(sta,sta+'\n            observe_time(native,out,logical_pins,pin_label,mapping)')
    source=source.replace(encoded,encoded+'\n        observe_io(native,out)')
    def observe_io(native,folder):
        data=capture_io(native);data['encoding_phase']='after_fresh_process_single_encoding'
        (folder/'extended_io_state.json').write_text(json.dumps(data,indent=2))
    namespace={'__name__':'isolated_timing_and_io_import','__file__':str(ROOT/'experiments/native_import_ownpr.py'),
               'observe_time':capture_time,'observe_io':observe_io}
    exec(compile(source,str(out/'adapter_source.py.txt'),'exec'),namespace)
    namespace['worker'](out,placement,True,True,False,True)


if __name__=='__main__':
    base.__file__=__file__;base.worker=worker;base.main()
    if '--worker' not in sys.argv:
        from ownpr_image_storage import sha
        out=Path(sys.argv[sys.argv.index('--out')+1]).resolve();m=json.loads((out/'manifest.json').read_text())
        m.update(bitstream_generated=(out/'probe.fs').is_file(),observation='time_pairs_before_and_extended_io_after_single_encoding',
            implementation_sha256={n:sha(ROOT/n) for n in ('scripts/native_io_extended_state.py','experiments/native_timing_state_probe.py')},
            extended_capture_file='extended_io_state.json',extended_capture_sha256=sha(out/'extended_io_state.json'))
        if m['bitstream_generated']:m['image_sha256']=sha(out/'probe.fs')
        (out/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps(dict(status=m['status'],bitstream_generated=m['bitstream_generated'])))
