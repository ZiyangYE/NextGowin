"""Decode independently calibrated BRAM C2Q and bypass-DSP data arcs only."""
import math
from ownpr_image_storage import load_json,sha
from ownpr_timing_pins import pins

def load(sta,cells,reverse,clock_bit,constants):
    from ownpr_hard_timing_model import HardCellTiming
    library=HardCellTiming();meta=load_json(sta/'manifest.json');index=load_json(sta/'hard_timing_index.json')
    if sha(sta/'hard_timing_index.json')!=meta['hard_timing_index_sha256'] or index['vendor_pnr_executed']:raise ValueError('Unverified own hard timing index')
    result={};sources=[];timed_inputs={};inactive_inputs={}
    for row in index['instances']:
        name=reverse[row['instance']];cell=cells[name];path=sta/row['file']
        if name in result or sha(path)!=row['sha256']:raise ValueError('Duplicate or changed hard timing capture')
        data=load_json(path)
        if not data['full_data_pins']:raise ValueError('Incomplete hard pin capture')
        inputs=dict(pins(cell,'input'));outputs=dict(pins(cell,'output'));arcs=[];used=set()
        for edge in data['actual_edges']:
            source,dest,kind=edge['source'],edge['destination'],edge['kind']
            matrix=library.matrices(cell,source,dest,kind)
            if matrix!=edge['matrices']:raise ValueError('Hard arc differs from independent calibration')
            used.update(p for p in (source,dest) if p in inputs)
            if kind in (20,22,25,27):continue # constraint checks, not propagation
            if source not in inputs or dest not in outputs:raise ValueError('Hard propagation direction mismatch')
            if kind==10:
                if inputs[source]!=clock_bit:raise ValueError('Only calibrated positive primary hard clock supported')
                transitions=[(0,0),(0,1)]
            elif kind==1 and cell['type']=='MULTALU27X18':transitions=[(0,0),(0,1),(1,0),(1,1)]
            else:raise ValueError('Uncalibrated hard propagation kind')
            slow=matrix[0]
            if len(slow)!=8 or slow[4:6]!=slow[6:8]:raise ValueError('Source-polarity-dependent hard matrix requires broader model')
            delay=slow[4:6]
            if not all(math.isfinite(v) and 0<=v<1e9 for v in delay):raise ValueError('Undefined hard delay')
            if inputs[source] in constants:continue
            arcs.append(dict(source=name+'/'+source,destination=name+'/'+dest,delay=delay,transitions=transitions,kind='hard_c2q' if kind==10 else 'hard_combinational'))
        result[name]=arcs;timed_inputs[name]=sorted(used);inactive_inputs[name]=sorted(set(inputs)-used);sources.append(dict(path=str(path),sha256=row['sha256']))
    return result,dict(index_sha256=sha(sta/'hard_timing_index.json'),independent_sources=library.sources,captures=sources,timed_input_pins=timed_inputs,inactive_input_pins=inactive_inputs,registered_dsp_supported=True,registered_dsp_scope='independently calibrated OREG CLK0/CE0/RESET0 SYNC only',negative_clock_supported=False)
