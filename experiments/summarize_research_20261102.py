"""Re-audit measured results; never equate implementation completion with closure."""
import json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'experiments')]
from project_timing import audit
from summarize_practical138 import coverage
from run_routing_ablation import pins
from run_abc_reproduction import sha

def main():
    result=dict(date='2026-11-02',status='audited',gowin_package_pins_reassigned=False,hardware_programmed=False,
        general_superiority_established=False,gowin_full_io_200mhz_achieved=False,artix=[],cpu=[])
    reference_pins=None;source_sha=None
    names=['artix7_fullio200_reference_004','artix7_fastio200_reference_001','artix7_fastio_mmcm200_reference_001','artix7_fastio_mmcm180_reference_001']
    for name in names:
        f=ROOT/'results'/name;m=json.loads((f/'manifest.json').read_text())
        if m['status']!='complete':raise ValueError('Incomplete reference')
        actual={r.split('\t')[0]:r.split('\t')[1] for r in (f/'ports.tsv').read_text().splitlines()}
        if reference_pins is None:reference_pins=actual;source_sha=m['source_sha256']
        if actual!=reference_pins or actual!=m['pins'] or m['source_sha256']!=source_sha:raise ValueError('Reference source/pins changed')
        checks=dict((k,int(v)) for k,v in re.findall(r'checking (\w+) \((\d+)\)',(f/'check_timing.rpt').read_text()))
        required={'no_clock','unconstrained_internal_endpoints','no_input_delay','no_output_delay','partial_input_delay','partial_output_delay','loops','generated_clocks'}
        if not required<=checks.keys() or any(checks.values()):raise ValueError('Incomplete reference constraints')
        xdc=(f/'fullio.xdc').read_text()
        for direction in ('input','output'):
            for limit in ('max','min'):
                if f'set_{direction}_delay -{limit} 0 -clock clk' not in xdc:raise ValueError('Missing I/O bound')
        if any(x in (f/'effective.xdc').read_text() for x in ('set_false_path','set_multicycle_path')):raise ValueError('Unexpected timing exception')
        text=(f/'timing_summary.rpt').read_text();summary=text.split('TNS Total Endpoints',1)[1]
        numbers=next(line.split() for line in summary.splitlines() if len(line.split())==12 and re.match(r'\s*-?\d+\.\d+',line))
        wns,whs,pws=map(float,(numbers[0],numbers[4],numbers[8]));failures=dict(setup=int(numbers[2]),hold=int(numbers[6]),pulse=int(numbers[10]))
        route=(f/'route_status.rpt').read_text()
        if not re.search(r'# of nets with routing errors\.+\s*:\s*0\b',route):raise ValueError('Routing incomplete')
        drc=(f/'drc.rpt').read_text()
        if re.search(r'\|\s*(?:Error|Critical Warning)\s*\|',drc):raise ValueError('Reference DRC error')
        result['artix'].append(dict(folder=name,target_mhz=m['target_mhz'],clock=m.get('clock_conditioning','direct_BUFG'),fastio=m.get('fastio',False),setup_wns_ns=wns,hold_whs_ns=whs,pulse_wpws_ns=pws,violations=failures,
            full_io_sta_closed=not any(failures.values()) and min(wns,whs,pws)>=0,check_timing=checks,
            limitation='Not a board signoff or an identified A9 device; MMCM assumes lock, default vendor output load; CFGBVS configuration warning retained.',
            reports_sha256={n:sha(f/n) for n in ['timing_summary.rpt','check_timing.rpt','route_status.rpt','drc.rpt','ports.tsv','effective.xdc']},wall_seconds=m['wall_seconds']))
    cohort=json.loads((ROOT/'results/cpu_dsp_enable_ablation_001/manifest.json').read_text())
    if cohort['status']!='complete':raise ValueError('Ablation incomplete')
    rows=list(cohort['rows'])
    portfolio=ROOT/'results/cpu_dsp_inverted_portfolio_001';pm=json.loads((portfolio/'manifest.json').read_text())
    if pm['status']!='complete' or not pm['timing_closed']:raise ValueError('Portfolio integration failed')
    rows.append(dict(name='packed_portfolio',target_mhz=82,physical_folder=str((portfolio/'lut4/06_pnr').relative_to(ROOT))))
    frozen=ROOT/'results/tangprimer138_cpu_002/vexriscv/vendor_pnr100/mapped.pin.html'
    for row in rows:
        f=ROOT/row['physical_folder'];p=audit(f,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':1000/row['target_mhz']}))
        if pins(f/'mapped.pin.html')!=pins(frozen):raise ValueError('Gowin I/O changed')
        result['cpu'].append(dict(name=row['name'],target_mhz=row['target_mhz'],folder=str(f.relative_to(ROOT)),closed=p['timing_closed'],violations=p['violated_endpoints'],frequency=p['frequency_reports'],coverage=coverage(f),resources=p['implementation'],netlist_sha256=sha(f/'mapped.vg'),sdc_sha256=sha(f/'project.sdc'),cst_sha256=sha(f/'project.cst')))
    result['cpu_cold_seconds']=json.loads((ROOT/'results/cpu_dsp_inverted_cold_001/run/manifest.json').read_text())['wall_seconds']
    result['portfolio_seconds']=pm['total_wall_seconds']
    result['cpu_ablation_seconds']=cohort['wall_seconds']
    target=ROOT/'results/research_20261102_verified_summary.json';target.write_text(json.dumps(result,indent=2)+'\n')
    print('Audited',len(result['artix']),'Artix references and',len(result['cpu']),'CPU results;',target)
if __name__=='__main__':main()
