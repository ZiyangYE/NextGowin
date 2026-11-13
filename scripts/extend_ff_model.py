"""Add a narrowly validated rising-clock DFFRE data setup/hold envelope."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from capture_runtime_tables import EXPECTED
from gowin_probe import ROOT
from extract_timing import extract
from validate_timing_model import validate


def extend(model,trace,trace_directory):
    if model.get('dll_sha256')!=EXPECTED or trace.get('dll_sha256')!=EXPECTED:
        raise ValueError('Unverified DLL build')
    if trace.get('status')!='complete' or trace.get('returncode')!=0:
        raise ValueError('Incomplete FF query capture')
    for field,name in [('command_sha256','trace.cdb'),('query_script_sha256','query.cdb')]:
        if hashlib.sha256((trace_directory/name).read_bytes()).hexdigest()!=trace[field]:
            raise ValueError('Trace command hash mismatch')
    log=trace_directory/'trace.log'
    if '\nFF_TRACE_COMPLETE\n' not in log.read_text(errors='replace'):
        raise ValueError('Missing trace completion marker')
    expected={24:(670,1266,0,1),4:(670,674,1,1),6:(670,674,0,1)}
    observed={}
    for row in trace['queries']:
        if row['selector']!=1 or int(row['caller_rva'].replace('`',''),16)!=0x144738d:
            raise ValueError('Unexpected native row selector/call site')
        observed[row['row']]=tuple(row[k] for k in ['source_pin_id','destination_pin_id','arg6','arg7'])
    if any(observed.get(k)!=v for k,v in expected.items()):
        raise ValueError('Missing or different FF data query signatures')
    result=copy.deepcopy(model)
    if result['ff_clock_to_q']['group']!=3:
        raise ValueError('Unexpected FF table group')
    rows={r['row']:r['values'] for r in result['runtime_rows'] if r['group']==3}
    # The audited dispatcher has three data-pin routing branches: 0/2, 4/6,
    # and 8/10 for rising-clock setup/hold. Runtime capture proves the 4/6
    # branch; additional physical reports distinguish the alternate row set.
    audit_directory=ROOT/'binary_audit/timing'
    audit=json.loads((audit_directory/'manifest.json').read_text())
    if audit['sha256']!=EXPECTED:
        raise ValueError('FF dispatcher audit DLL mismatch')
    required={'internal_ff_arc_dispatch':'0x1446fc0','internal_cell_delay_lookup':'0x1444780'}
    disassembly={}
    for symbol,rva in required.items():
        entry=next((e for e in audit['evidence'] if e['symbol']==symbol and e['rva']==rva),None)
        if entry is None:raise ValueError('Missing FF dispatcher/lookup audit')
        disassembly[symbol]=hashlib.sha256((audit_directory/entry['file']).read_bytes()).hexdigest()
    setup_rows=[0,4,8];hold_rows=[2,6,10]
    setup=[v for r in setup_rows for v in rows[r][6:8]]
    hold=[v for r in hold_rows for v in rows[r][8:10]]
    result['schema']='gowin138-synthesis-envelope-v3'
    result['ff_data_constraints']=dict(cell='DFFRE',clock_edge='rising',data_pin='D',group=3,
        scope='Rising-clock DFFRE data-pin envelope across three audited routing branches; no per-instance branch or transition assignment. CE/reset, falling-clock and other FF variants unsupported.',
        setup=dict(pin_row_ids=setup_rows,runtime_rows=setup_rows,values_ns=setup,envelope_ps=math.ceil(max(setup)*1000),report_adjustment_sign=-1),
        hold=dict(pin_row_ids=hold_rows,runtime_rows=hold_rows,values_ns=hold,envelope_ps=math.ceil(max(hold)*1000),report_adjustment_sign=1),
        evidence=dict(trace_manifest_sha256=hashlib.sha256(json.dumps(trace,sort_keys=True).encode()).hexdigest(),
            trace_log_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),
            disassembly_sha256=disassembly,
            query_signatures={str(k):v for k,v in expected.items()},
            dispatch_rva='0x1446fc0',lookup_rva='0x1444780'))
    return result


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--model',type=Path,default=Path('results/timing/gowin138_model.json'))
    ap.add_argument('--trace',type=Path,default=Path('results/gowin_ff_trace_004/manifest.json'))
    ap.add_argument('--pair',nargs=2,action='append',required=True)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    model=extend(json.loads(args.model.read_text()),json.loads(args.trace.read_text()),args.trace.parent)
    checks=validate(model,[extract(Path(a),Path(b)) for a,b in args.pair])
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.with_suffix('.validation.json').write_text(json.dumps(checks,indent=2)+'\n')
    kinds={c['kind'] for c in checks['checks']}
    if not checks['all_matched'] or not {'DFFRE D setup','DFFRE D hold'}<=kinds:
        raise ValueError('Setup/hold report cross-validation failed or incomplete')
    model['ff_report_validation']=checks
    args.out.write_text(json.dumps(model,indent=2)+'\n')
    print(json.dumps(dict(ff_data_constraints=model['ff_data_constraints'],checked=checks['checked'],matched=checks['matched']),indent=2))


if __name__=='__main__':main()
