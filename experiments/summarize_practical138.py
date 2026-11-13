"""Recheck complete I/O coverage and report only actually closed test points."""
import html,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from project_timing import audit
from run_routing_ablation import pins
from run_abc_reproduction import sha

def coverage(folder):
    sdc=(folder/'project.sdc').read_text()
    if any(word in sdc for word in ('set_false_path','set_multicycle_path')):raise ValueError('Excluded paths cannot qualify for full-I/O acceptance')
    groups={}
    for kind,mode,names in re.findall(r'set_(input|output)_delay\s+-(max|min)\s+0\s+-clock\s+\w+\s+\[get_ports\s+\{([^}]+)\}\]',sdc):groups[kind,mode]=names.split()
    clocks=set(re.findall(r'create_clock[^\n]*\[get_ports\s+\{([^}]+)\}\]',sdc))
    text=(folder/'mapped.pin.html').read_text().split('<a name="Pinout_by_Port_Name">',1)[1].split('</table>',1)[0];count=0
    for row in re.findall(r'<tr>(.*?)</tr>',text,re.S):
        fields=[html.unescape(re.sub('<[^>]+>','',v)).strip() for v in re.findall(r'<td[^>]*>(.*?)</td>',row,re.S)]
        if len(fields)!=19 or fields[4] not in ('in','out','inout'):continue
        name=fields[0].removeprefix('\\')
        if name in clocks:continue
        for direction in (['input','output'] if fields[4]=='inout' else ['input' if fields[4]=='in' else 'output']):
            for mode in ('max','min'):
                if not any(re.fullmatch(re.escape(pattern).replace(r'\[\*\]',r'\[\d+\]'),name) for pattern in groups.get((direction,mode),[])):raise ValueError('Unconstrained external port '+name+'/'+direction+'/'+mode)
        count+=1
    return dict(all_reported_nonclock_pins_have_max_min_constraints=True,constrained_pin_count=count,data_false_paths=False,multicycle_paths=False)

def main():
    result=dict(status='audited',scope='Fixed package pins and 3.3-V/8-mA settings within each design. Complete external I/O constraints, native setup/hold/recovery/removal/pulse checks. Tested operating points, not exact maximum frequency or board protocol validation.',rows=[],significant_general_advantage_established=False,full_io_200mhz_achieved=False)
    selected=[('int2float','vendor','fullio_closure_003','int_vendor_local145_edge'),('int2float','own','fullio_closure_003','int_lut4_local155_edge'),('uart','vendor','fullio_closure_003','uart_vendor135_edge'),('uart','own','fullio_closure_003','uart_own140_edge'),('tmds','vendor','fullio_closure_003','tmds_vendor135_edge'),('tmds','own','fullio_closure_003','tmds_own135_edge'),('vexriscv','vendor','fullio_closure_003','cpu_vendor80_edge'),('vexriscv','own','fullio_challengers_001','cpu_lut482'),('lcd','vendor','fullio_closure_003','lcd_vendor60_core_edge'),('lcd','own','fullio_closure_003','lcd_wide60_core_edge')]
    for design,side,batch,name in selected:
        base=ROOT/'results'/batch;folder=base/name;m=json.loads((base/'manifest.json').read_text());row=next(r for r in m['runs'] if r['name']==name)
        target=row.get('target_mhz',row.get('target'));p=audit(folder,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':1000/target}))
        if not p['timing_closed']:raise ValueError('Selected point did not close')
        reference=ROOT/'results/general_synthesis_suite_012/int2float/vendor_pnr' if design=='int2float' else ROOT/'results/tangprimer138_cpu_002/vexriscv/vendor_pnr100' if design=='vexriscv' else ROOT/'results/tangprimer138_lcd_002/lcd/vendor_pnr100' if design=='lcd' else ROOT/'results/tangprimer138_suite_001'/design/'vendor_pnr100'
        actual=pins(folder/'mapped.pin.html')
        if actual!=pins(reference/'mapped.pin.html'):raise ValueError('Fixed I/O verification failed')
        result['rows'].append(dict(design=design,side=side,target_mhz=target,reported_fmax=p['frequency_reports']['clk']['reported_fmax_mhz'],timing_closed=True,violations=p['violated_endpoints'],io_coverage=coverage(folder),resources=p['implementation'],physical_folder=str(folder.relative_to(ROOT)),sdc_sha256=sha(folder/'project.sdc'),cst_sha256=sha(folder/'project.cst'),netlist_sha256=sha(folder/'mapped.vg')))
    result['fresh_cold_pair_record']='results/int2float_fresh_pair_001/manifest.json'
    result['no_reassignment_after_baseline_pins_frozen']=True
    (ROOT/'results/practical138_verified_summary_001.json').write_text(json.dumps(result,indent=2)+'\n')
    excluded=ROOT/'results/int2float_core200_probe_001/NOT_ACCEPTED.json';excluded.write_text(json.dumps(dict(status='diagnostic_only_not_accepted',reason='User explicitly requires external I/O. This earlier probe excluded input/output paths and is not a 200-MHz acceptance result.'),indent=2)+'\n')
    print([(r['design'],r['side'],r['target_mhz']) for r in result['rows']])
if __name__=='__main__':main()
