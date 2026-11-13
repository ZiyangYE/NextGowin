"""Re-audit every completed routing ablation and preserve complete evidence."""
import json
import math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from project_timing import audit
from routing_path_diagnosis import diagnose
from run_routing_ablation import pins
from run_abc_reproduction import sha

def main():
    result=dict(scope='Development experiments, not held-out general superiority. Reported Fmax is not a closed-frequency search.',runs=[],comparison=[])
    for batch in ['routing_ablation_002','output_iob_ablation_001','compact_output_ablation_001']:
        base=ROOT/'results'/batch;m=json.loads((base/'manifest.json').read_text())
        if m['status'] not in ('complete','completed_with_failures'):raise ValueError('Unfinished batch')
        for row in m['runs']:
            copy=dict(batch=batch,design=row['design'],variant=row.get('variant','lut4_output100'),status=row['status'])
            if row['status']!='complete':
                copy['error']=row['error']
                failed=base/row['design']/row['variant']
                if not (failed/'manifest.json').exists():failed=ROOT/'results/routing_ablation_001'/row['design']/row['variant']
                native=json.loads((failed/'manifest.json').read_text())
                log=(failed/'pnr.log').read_text()
                if native.get('returncode')!=1 or 'access violation reading 0x0000000000000008' not in log:raise ValueError('Failure was not a verified native access violation')
                copy.update(failure_evidence=str((failed/'pnr.log').relative_to(ROOT)),failure_log_sha256=sha(failed/'pnr.log'),failure_class='native_access_violation')
                if 'previous_attempt' in row:copy['previous_attempt']=row['previous_attempt']
                result['runs'].append(copy);continue
            folder=base/row['design']/(row['variant'] if 'variant' in row else '06_pnr')
            period=6.25 if '160' in copy['variant'] else 10.
            physical=audit(folder,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':period}))
            reference=pins(ROOT/'results/general_synthesis_suite_012'/row['design']/'vendor_pnr/mapped.pin.html')
            if pins(folder/'mapped.pin.html')!=reference:raise ValueError('Actual pin/electrical mismatch')
            (folder/'physical_audit.json').write_text(json.dumps(physical,indent=2)+'\n')
            paths=diagnose((folder/'mapped.tr').read_text());(folder/'routing_diagnosis.json').write_text(json.dumps(paths,indent=2)+'\n')
            copy.update(fmax_mhz=physical['frequency_reports']['clk']['reported_fmax_mhz'],violations=physical['violated_endpoints'],timing_closed=physical['timing_closed'],resources=physical['implementation'],worst_path=paths[0],pins_verified=True,physical_audit_sha256=sha(folder/'physical_audit.json'))
            if copy['fmax_mhz']!=row['fmax_mhz'] or copy['violations']!=row['violations']:raise ValueError('Re-audit differs from recorded result')
            result['runs'].append(copy)
    idx={(r['design'],r['variant']):r for r in result['runs']}
    for name in ['ctrl','int2float','router']:
        v=idx[name,'vendor_output100'];wide=idx[name,'own_output100'];compact=idx[name,'lut4_output100']
        if not all(r['timing_closed'] for r in (v,wide,compact)):raise ValueError('Selection requires closed original target')
        best=max((wide,compact),key=lambda r:r['fmax_mhz'])
        result['comparison'].append(dict(design=name,vendor_core100=idx[name,'vendor_core100']['fmax_mhz'],own_core100=idx[name,'own_core100']['fmax_mhz'],vendor_output100=v['fmax_mhz'],wide_output100=wide['fmax_mhz'],lut4_output100=compact['fmax_mhz'],selected=best['variant'],ratio=best['fmax_mhz']/v['fmax_mhz']))
    result.update(selected_geomean=math.prod(r['ratio'] for r in result['comparison'])**(1/3),
        completed_native_runs=sum(r['status']=='complete' for r in result['runs']),
        failed_native_runs=sum(r['status']=='failed' for r in result['runs']),
        timing_closed_runs=sum(r.get('timing_closed',False) for r in result['runs']),
        generic_high_frequency_goal_achieved=False)
    (ROOT/'results/routing_optimization_evidence_001.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('runs',)},indent=2))

if __name__=='__main__':main()
