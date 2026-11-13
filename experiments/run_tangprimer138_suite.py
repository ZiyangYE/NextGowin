"""Pinned real RTL components, 138K, complete external I/O timing."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from export_gowin_frontend import export
from project_timing import audit
from run_abc_reproduction import sha
from flow_budget import Budget,activate
from run_routing_ablation import pins

def run_logged(cmd,log,timeout):
    with log.open('w') as stream:subprocess.run(cmd,stdout=stream,stderr=subprocess.STDOUT,check=True,timeout=timeout)

def prepare(name,p):
    repo=ROOT/'third_party/tangprimer25k';include=[]
    if name=='uart':
        sources=[repo/'UART/simple_uart/src/uart_tx.v',repo/'UART/simple_uart/src/uart_rx.v']
        wrapper=p/'uart_duplex.v';wrapper.write_text('''module top(input clk,input rst_n,input rx,input [7:0] tx_data,input tx_valid,input rx_ready,
output tx,output tx_ready,output [7:0] rx_data,output rx_valid);
uart_tx #(.CLK_FRE(200),.BAUD_RATE(115200)) sender(.clk(clk),.rst_n(rst_n),.tx_data(tx_data),.tx_data_valid(tx_valid),.tx_data_ready(tx_ready),.tx_pin(tx));
uart_rx #(.CLK_FRE(200),.BAUD_RATE(115200)) receiver(.clk(clk),.rst_n(rst_n),.rx_data(rx_data),.rx_data_valid(rx_valid),.rx_data_ready(rx_ready),.rx_pin(rx));
endmodule
''');sources.append(wrapper);top='top'
        inputs=['rst_n','rx','tx_data[*]','tx_valid','rx_ready'];outputs=['tx','tx_ready','rx_data[*]','rx_valid']
    elif name=='tmds':
        sources=[repo/'hdmi/svo/src/hdmi/svo_tmds.v'];include=[sources[0].parent];top='svo_tmds'
        inputs=['resetn','de','ctrl[*]','din[*]'];outputs=['dout[*]']
    elif name=='lcd':
        original=repo/'pmod_lcd/src/top.v';adapted=p/'lcd_defined_outputs.v';text=original.read_text()
        if 'assign ser_tx' in text:raise ValueError('Unexpected LCD serial output implementation')
        text=text.replace('assign resetn = !rst;',"assign resetn = !rst;\nassign ser_tx = 1'b0;",1);adapted.write_text(text)
        (p/'source_adaptation.json').write_text(json.dumps(dict(original=str(original.relative_to(ROOT)),original_sha256=sha(original),change='Tie the originally undriven, unused serial_tx output to zero; LCD logic unchanged and same adapted RTL on both sides.',adapted_sha256=sha(adapted)),indent=2)+'\n')
        sources=[adapted];top='lcd114_test'
        inputs=['rst','ser_rx'];outputs=['ser_tx','lcd_resetn','lcd_clk','lcd_cs','lcd_dc','lcd_data']
    else:
        original=repo/'litex/src/VexRiscv.v';adapted=p/'VexRiscv_defined_outputs.v';text=original.read_text()
        old="assign iBusWishbone_DAT_MOSI = 32'bxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx;"
        if text.count(old)!=1:raise ValueError('Unexpected VexRiscv unused instruction-write port')
        adapted.write_text(text.replace(old,"assign iBusWishbone_DAT_MOSI = 32'b0;"))
        (p/'source_adaptation.json').write_text(json.dumps(dict(original=str(original.relative_to(ROOT)),original_sha256=sha(original),change='Resolve only the explicitly all-X instruction-bus write-data output to zero. All defined RTL behavior retained; same adapted RTL for both flows.',adapted_sha256=sha(adapted)),indent=2)+'\n')
        sources=[adapted];top='VexRiscv'
        inputs=['externalResetVector[*]','timerInterrupt','softwareInterrupt','externalInterruptArray[*]','iBusWishbone_ACK','iBusWishbone_DAT_MISO[*]','iBusWishbone_ERR','dBusWishbone_ACK','dBusWishbone_DAT_MISO[*]','dBusWishbone_ERR','reset']
        outputs=[bus+'_'+port+('[*]' if port in ('ADR','DAT_MOSI','SEL','CTI','BTE') else '') for bus in ('iBusWishbone','dBusWishbone') for port in ('CYC','STB','WE','ADR','DAT_MOSI','SEL','CTI','BTE')]
    tree=ET.Element('Project');ET.SubElement(tree,'Device',partNumber='GW5AST-LV138FPG676AC1/I0');fl=ET.SubElement(tree,'FileList')
    for source in sources:ET.SubElement(fl,'File',path=str(source),type='verilog')
    opts=ET.SubElement(tree,'OptionList');ET.SubElement(opts,'Option',type='top_module',value=top)
    for path in include:ET.SubElement(opts,'Option',type='include_path',value=str(path))
    ET.ElementTree(tree).write(p/'frontend.prj',encoding='utf-8',xml_declaration=True)
    for freq in (100,200):
        sdc=f'create_clock -name clk -period {1000/freq:g} [get_ports {{clk}}]\n'
        for direction,ports in [('input',inputs),('output',outputs)]:
            for mode in ('max','min'):sdc+=f'set_{direction}_delay -{mode} 0 -clock clk [get_ports {{{" ".join(ports)}}}]\n'
        sdc+='set_clock_uncertainty -hold 0.050 -from [get_clocks {clk}] -to [get_clocks {clk}]\n'
        (p/f'clock{freq}.sdc').write_text(sdc)
        (p/f'profile{freq}.json').write_text(json.dumps(dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':1000/freq})))
    return dict(top=top,input_ports=inputs,output_ports=outputs,source_sha256={str(s.relative_to(ROOT)):sha(s) for s in sources})

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--designs',nargs='+',choices=['uart','tmds','vexriscv','lcd'],default=['uart','tmds','vexriscv']);a=ap.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False);report=dict(status='running',repository_commit='27c1737b687369f46baff9cf621cd7499a1b7fd4',scope='Real source components; complete external input/output constraints, no data false paths. 138K timing implementation, not board-level protocol or hardware acceptance.',runs=[])
    def save():(out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    save()
    for name in a.designs:
        p=out/name;p.mkdir();row=dict(design=name,status='running');report['runs'].append(row);save();budget=Budget(3600);activate(budget)
        try:
            row['source']=prepare(name,p);save();print(name,'frontend',flush=True)
            export(p/'frontend.prj',p/'frontend',timeout=180)
            run_logged([sys.executable,str(ROOT/'scripts/native_project_synthesis.py'),'--frontend',str(p/'frontend'),'--sdc',str(p/'clock200.sdc'),'--out',str(p/'vendor_synthesis')],p/'vendor_synthesis.log',budget.timeout(600))
            def physical(folder,period,cst=None):
                cmd=[sys.executable,str(ROOT/'scripts/run_gowin_netlist.py'),str(p/'vendor_synthesis/mapped.vg'),'--sdc',str(p/f'clock{period}.sdc'),'--out',str(folder),'--output-iob-registers','--timeout',str(int(budget.timeout(420,reserve=10)))]
                if cst:cmd+=['--cst',str(cst)]
                run_logged(cmd,p/(folder.name+'.log'),budget.remaining())
                result=audit(folder,json.loads((p/f'profile{period}.json').read_text()));(folder/'physical_audit.json').write_text(json.dumps(result,indent=2)+'\n');return result
            seed=physical(p/'pin_seed100',100);reference=pins(p/'pin_seed100/mapped.pin.html');cst=p/'fixed.cst';lines=[]
            for port,r in reference.items():
                lines.append(f'IO_LOC "{port}" {r["pin"]};');attrs=f'IO_TYPE={r["io_type"]} PULL_MODE={r["pull"]}'
                if r['drive'].isdigit():attrs+=' DRIVE='+r['drive']
                lines.append(f'IO_PORT "{port}" {attrs};')
            cst.write_text('\n'.join(lines)+'\n');row['vendor']={}
            for freq in (100,200):
                v=physical(p/f'vendor_pnr{freq}',freq,cst)
                if pins(p/f'vendor_pnr{freq}'/'mapped.pin.html')!=reference:raise ValueError('Vendor actual pin/electrical mismatch')
                row['vendor'][str(freq)]=dict(frequency=v['frequency_reports'],timing_closed=v['timing_closed'],violations=v['violated_endpoints'],resources=v['implementation']);save()
            cfg=dict(project=str(p/'frontend.prj'),sdc=str(p/'clock200.sdc'),cst=str(cst),physical_profile=str(p/'profile200.json'),period_ps=5000,strategy='arch_delay',resource_optimizations=True,register_alternatives=True,register_control_penalty_ps=0,soft_comparisons=False,logic_replication=False,register_replication=False,no_iob_registers=False,output_iob_registers=True,timeout=600,total_timeout=3600)
            config=p/'config.json';config.write_text(json.dumps(cfg,indent=2)+'\n');print(name,'own candidates',flush=True)
            run_logged([sys.executable,str(ROOT/'scripts/nextgowin_portfolio.py'),'--config',str(config),'--out',str(p/'own'),'--recipes','wide','lut4','--total-timeout',str(int(budget.remaining()-10))],p/'own_runner.log',budget.remaining())
            own=json.loads((p/'own/manifest.json').read_text());row['own']=own
            for c in own['candidates']:
                if c['status']=='complete' and pins(p/'own'/c['recipe']/'06_pnr/mapped.pin.html')!=reference:raise ValueError('Own actual pin/electrical mismatch')
            row['status']='complete';print(name,'DONE',row['vendor']['200']['frequency'],own['selected_recipe'],flush=True)
        except Exception as e:row.update(status='failed',error=repr(e));print(name,'FAILED',repr(e),flush=True)
        row['wall_seconds']=budget.used();save()
    report['status']='complete' if all(r['status']=='complete' for r in report['runs']) else 'completed_with_failures';save()

if __name__=='__main__':main()
