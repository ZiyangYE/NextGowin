"""Read hash-pinned native synchronous arrival/required state pairs.

Raw pair slots are not assumed to be rise/fall. Interpretations require
independent agreement with complete reports and native through-pin slacks.
The imported placement/routes are never changed; no P&R or encoding runs.
"""
import json,math,sys,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import native_pin_feedback_probe as base


def capture(native,out,logical_pins,pin_label,mapping,control=False):
    import ctypes as C
    expected='bc9472a5a22cf4a94e39e07439f261f3354c668f92ef9abc735e6678bea53b7b'
    from gowin_probe import BIN
    if hashlib.sha256((BIN/'gowin.dll').read_bytes()).hexdigest()!=expected:raise ValueError('Timing-state ABI hash changed')
    fn=native.fn('?getSinkSlack@TmMonitor@STA@@QEAAMPEAVPin@NETLIST@@_N@Z',C.c_float,[C.c_void_p,C.c_void_p,C.c_bool])
    def vector(ptr,offset,limit):
        begin,end=native.ptr(ptr+offset),native.ptr(ptr+offset+8)
        if end<begin or (end-begin)%8 or (end-begin)//8>limit:raise ValueError('Unverified timing vector shape')
        return [native.ptr(begin+8*i) for i in range((end-begin)//8)]
    rows=[];seen=set()
    for name,direction,pin,_ in ([] if control else logical_pins):
        if pin in seen:continue
        seen.add(pin);vertex=native.ptr(pin+0x20)
        if not vertex:continue
        selected={str(int(mode)):float(fn(native.monitor,pin,mode)) for mode in (False,True)}
        groups=[]
        for group in vector(vertex,0x28,32):
            if not group:raise ValueError('Null timing group')
            kind=C.c_uint8.from_address(group+8).value
            if kind!=1:continue
            states=[]
            for state in vector(group,0x10,128):
                if not state:raise ValueError('Null timing state')
                flags=C.c_uint16.from_address(state+0x18).value
                width=1 if flags&8 else 2 if flags&16 else 4 if flags&32 else 0
                data=native.ptr(state+0x10);pairs=[]
                if width and not data:raise ValueError('Null time-pair storage')
                for i in range(width):
                    arrival=float(C.c_float.from_address(data+8*i).value);required=float(C.c_float.from_address(data+8*i+4).value)
                    if not math.isfinite(arrival) or not math.isfinite(required):raise ValueError('Nonfinite timing-state pair')
                    pairs.append(dict(arrival_ns=arrival,required_ns=required))
                states.append(dict(flags=flags,pairs=pairs))
            groups.append(dict(kind=kind,states=states))
        if groups:rows.append(dict(instance=name,native_name=mapping['instances'][name],pin=pin_label(pin),direction=direction,selected_slack=selected,groups=groups))
    result=dict(schema='native-sync-time-pairs-observation-v1',pins=rows,dll_sha256=expected,
                abi=dict(pin_vertex_offset=0x20,vertex_group_vector_offset=0x28,group_kind_offset=8,group_state_vector_offset=0x10,state_flags_offset=0x18,state_pairs_offset=0x10),
                evidence_rvas=['0x137f090','0x13bbda0','0x141a4d0','0x141a560','0x141a9c0'],
                scope='Raw read-only synchronous timing contexts; no rise/fall, required-time or optimization meaning promoted before validation',vendor_pnr_executed=False)
    (out/'pin_feedback.json').write_text(json.dumps(result,separators=(',',':')))
    print('NATIVE_TIMING_STATES_CAPTURED',len(rows),flush=True)


if __name__=='__main__':
    # Reuse the isolated importer and exact source-hash checks. The worker
    # launch points back to this file; only the observation callback changes.
    base.__file__=__file__;base.capture=capture;base.main()
