"""Fixed Artix-7 reference, same registered RTL and complete I/O timing.

This is not a pin-compatible silicon/process experiment. Gowin pins are never
changed. The new Artix reference uses one predeclared board-header assignment.
"""
import argparse,ctypes,json,subprocess,time,shutil,tempfile
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from run_abc_reproduction import sha

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--frequency',type=float,default=200);ap.add_argument('--scratch-temp',action='store_true');ap.add_argument('--precreate-realtime',action='store_true');ap.add_argument('--fastio',action='store_true');ap.add_argument('--mmcm',action='store_true');a=ap.parse_args()
    out=a.out.resolve();out.mkdir(exist_ok=False);source=ROOT/'results/fanout_development_suite_001/int2float/rtl.v'
    work=Path(tempfile.mkdtemp(prefix='ngvivado_')).resolve() if a.scratch_temp else out
    used_source=work/'input.v' if a.scratch_temp else source
    if a.scratch_temp:shutil.copy2(source,used_source)
    if a.mmcm:
        used_source=work/'input_mmcm.v';original=source.read_text()
        if original.count('module top(')!=1:raise ValueError('Unexpected reference RTL wrapper')
        wrapper=f'''\nmodule top(input clk,input [10:0] a,output [6:0] q);
wire fb_raw,fb,ck_raw,ck,locked;
MMCME2_BASE #(.CLKIN1_PERIOD({1000/a.frequency:.9f}),.CLKFBOUT_MULT_F(5.0),.DIVCLK_DIVIDE(1),.CLKOUT0_DIVIDE_F(5.0)) clock_manager
(.CLKIN1(clk),.CLKFBIN(fb),.CLKFBOUT(fb_raw),.CLKOUT0(ck_raw),.LOCKED(locked),.PWRDWN(1'b0),.RST(1'b0));
BUFG feedback_buf(.I(fb_raw),.O(fb));BUFG clock_buf(.I(ck_raw),.O(ck));
core dut(.clk(ck),.a(a),.q(q));
endmodule\n'''
        used_source.write_text(original.replace('module top(','module core(',1)+wrapper)
    # Arty A7-100 Rev D/E E3 clock, then JA/JB/JC header order. No pin search.
    pads=['G13','B11','A11','D12','D13','B18','A18','K16','E15','E16','D15','C15','J17','J18','K15','J15','U12','V12']
    mapping={'clk':'E3',**{f'a[{i}]':pads[i] for i in range(11)},**{f'q[{i}]':pads[11+i] for i in range(7)}}
    xdc=out/'fullio.xdc';lines=[f'create_clock -name clk -period {1000/a.frequency:.9f} [get_ports {{clk}}]',
        'set_input_delay -max 0 -clock clk [get_ports {a[*]}]','set_input_delay -min 0 -clock clk [get_ports {a[*]}]',
        'set_output_delay -max 0 -clock clk [get_ports {q[*]}]','set_output_delay -min 0 -clock clk [get_ports {q[*]}]',
        'set_clock_uncertainty -setup 0 [get_clocks clk]','set_clock_uncertainty -hold 0.050 [get_clocks clk]',
        'set_property IOSTANDARD LVCMOS33 [get_ports *]','set_property DRIVE 8 [get_ports {q[*]}]',
        'set_property SLEW '+('FAST' if a.fastio else 'SLOW')+' [get_ports {q[*]}]','set_property IOB TRUE [get_ports {q[*]}]',
        'set_property IOB FALSE [get_ports {a[*]}]']
    lines += [f'set_property PACKAGE_PIN {pin} [get_ports {{{port}}}]' for port,pin in mapping.items()];xdc.write_text('\n'.join(lines)+'\n')
    if a.fastio:
        with xdc.open('a') as f:f.write('set_property IBUF_LOW_PWR FALSE [get_ports {clk a[*]}]\n')
    used_xdc=work/xdc.name
    if a.scratch_temp:shutil.copy2(xdc,used_xdc)
    script=out/'run.tcl';script.write_text(f'''set_param general.maxThreads 1
create_project -in_memory -part xc7a100tcsg324-1
read_verilog {{{used_source.as_posix()}}}
read_xdc {{{used_xdc.as_posix()}}}
synth_design -top top -part xc7a100tcsg324-1
set_clock_uncertainty -setup 0 [get_clocks *]
set_clock_uncertainty -hold 0.050 [get_clocks *]
opt_design
place_design
phys_opt_design
route_design
report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose -max_paths 25 -file timing_summary.rpt
report_timing -delay_type max -path_type full_clock_expanded -input_pins -nets -max_paths 25 -file setup_paths.rpt
report_timing -delay_type min -path_type full_clock_expanded -input_pins -nets -max_paths 25 -file hold_paths.rpt
report_timing -delay_type max -from [all_registers] -to [all_registers] -path_type full_clock_expanded -input_pins -nets -max_paths 10 -file internal_paths_diagnostic.rpt
report_clocks -file clocks.rpt
report_utilization -file utilization.rpt
report_io -file io.rpt
report_route_status -file route_status.rpt
report_drc -file drc.rpt
check_timing -verbose -file check_timing.rpt
report_property -all -file output_properties.rpt [get_ports {{q[0]}}]
write_checkpoint -force routed.dcp
write_verilog -force -mode funcsim routed.v
write_xdc -force effective.xdc
set fp [open metrics.tsv w]
puts $fp "version\t[version -short]"
puts $fp "part\t[get_property PART [current_design]]"
foreach kind {{max min}} {{
  set paths [get_timing_paths -delay_type $kind -max_paths 1000 -slack_lesser_than 0]
  puts $fp "violating_paths_$kind\t[llength $paths]"
  set worst [get_timing_paths -delay_type $kind -max_paths 1]
  if {{[llength $worst]}} {{puts $fp "worst_slack_$kind\t[get_property SLACK $worst]"}}
}}
close $fp
set fp [open ports.tsv w]
foreach p [get_ports *] {{puts $fp [join [list $p [get_property PACKAGE_PIN $p] [get_property IOSTANDARD $p] [get_property DRIVE $p] [get_property SLEW $p] [get_property IOB $p]] "\t"]}}
close $fp
puts "NEXTGOWIN_REFERENCE_COMPLETE"
exit
''')
    used_script=work/script.name
    if a.precreate_realtime:
        text=script.read_text();old='synth_design -top top -part xc7a100tcsg324-1'
        replacement='file mkdir [file join [pwd] .Xil "Vivado-[pid]-[info hostname]" realtime tmp]\nif {[catch {'+old+'} err opts]} {puts $err; puts [dict get $opts -errorinfo]; exit 1}'
        script.write_text(text.replace(old,replacement,1))
    if a.scratch_temp:shutil.copy2(script,used_script)
    record=dict(status='running',working_directory=str(work),part='xc7a100tcsg324-1',target_mhz=a.frequency,source_sha256=sha(source),implementation_source_sha256=sha(used_source),clock_conditioning='MMCM_1to1_after_lock' if a.mmcm else 'direct_BUFG',fastio=a.fastio,xdc_sha256=sha(xdc),pins=mapping,
        pin_source='https://raw.githubusercontent.com/Digilent/digilent-xdc/master/Arty-A7-100-Master.xdc',
        scope='Same RTL, max/min I/O delay 0, hold uncertainty 50ps, 3.3V 8mA; different FPGA architecture/package/pins/corner and vendor pad-load model. Not the unidentified A9 board. No Gowin pin changes, no hardware programming.')
    (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');ctypes.windll.kernel32.SetErrorMode(0x8003);start=time.monotonic()
    try:
        with (out/'console.log').open('w') as log:cp=subprocess.run(['D:/Xilinx/Vivado/2022.2/bin/vivado.bat','-mode','batch','-nojournal','-nolog','-source',str(used_script)],cwd=work,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW,timeout=900)
        if a.scratch_temp:
            for p in work.iterdir():
                if p.is_file():shutil.copy2(p,out/p.name)
        record['returncode']=cp.returncode
        if cp.returncode or not (out/'metrics.tsv').exists():raise RuntimeError('Vivado implementation incomplete')
        metrics=dict(line.split('\t',1) for line in (out/'metrics.tsv').read_text().splitlines());record['metrics']=metrics
        actual={line.split('\t')[0]:line.split('\t')[1] for line in (out/'ports.tsv').read_text().splitlines()}
        if actual!=mapping:raise ValueError('Reference pin assignment changed')
        record['status']='complete'
    except Exception as e:record.update(status='failed',error=repr(e))
    record['wall_seconds']=time.monotonic()-start;(out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
if __name__=='__main__':main()
