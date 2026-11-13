"""Calibrate SDC-only final STA on a retained official P&R memory state.

Official placement/routing executes exactly once. Subsequent phases reparse
constraints and rebuild routed timing only. No result is own P&R, and the
experiment does not by itself freeze a fair baseline or certify hardware.
"""
import argparse,hashlib,json,os,re,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'experiments'))
from ownpr_image_storage import sha
from gowin_probe import BIN,prepare
import ctypes as C
from project_timing import parse,section
from ownpr_frequency_policy import target_evaluation_mhz
from ownpr_sta_invariants import compare_envelopes,MODES
from workspace_budget import reserve
from run_ownpr_pin_closure import stop_owned_process_tree


def clock_period(text,period):
    value,count=re.subn(r'(create_clock\s+-name\s+clk\s+-period\s+)[\d.]+',lambda m:m[1]+format(period,'.14g'),text)
    if count!=1:raise ValueError('Expected one primary clk')
    return value


def envelopes(text,mode):
    number={'setup':1,'hold':2,'recovery':3,'removal':4}[mode]
    details=section(text,f'3.3.{number} {mode.title()} Analysis Report');result={}
    for block in re.split(r'^\s*Path\d+\s*$',details,flags=re.M)[1:]:
        trace=re.search(r'Data Arrival Path:\n(.*?)(?=\nData Required Path:)',block,re.S)
        if not trace:raise ValueError('Missing native arrival path')
        rows=[m[1] for line in trace[1].splitlines() if (m:=re.match(r'^\s*[-\d.]+\s+[-\d.]+\s+t\w+\s+(?:(?:RR|RF|FR|FF)\s+)?\d+\s+\S+\s+(\S+)\s*$',line))]
        if not rows:raise ValueError('Empty native arrival path')
        key=rows[-1]
        values=[]
        for field in ('Data Arrival Time','Data Required Time','Slack'):
            match=re.search(r'^'+field+r'\s*:\s*([-\d.]+)',block,re.M)
            if not match:raise ValueError('Missing '+field)
            values.append(float(match[1]))
        if key in result:raise ValueError('Duplicate native endpoint '+key)
        result[key]=values
    if not result and mode in ('setup','hold'):raise ValueError('No '+mode+' envelopes')
    return result


def worker(out):
    from native_primitive_binding_oracle import worker as official
    # Existing hash-pinned official oracle retains native global state after
    # closing its log. No second native P&R task is issued below.
    official(out)
    dll=C.CDLL(str(BIN/'gowin.dll'));vp=C.c_void_p
    def fn(name,result,args):
        f=getattr(dll,name);f.restype=result;f.argtypes=args;return f
    ptr=lambda p:C.c_uint64.from_address(p).value
    mem=fn('?getGlobalMemPtr@@YAPEAVglobalMem@@XZ',vp,[])()
    monitor=fn('?getTmMonitor@globalMem@@QEAAPEAVTmMonitor@STA@@XZ',vp,[vp])(mem)
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(mem)
    if not mem or not monitor or not design:raise ValueError('Native state not retained')
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])
    pin_nodes=fn('?getNodebyPin@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z',C.c_bool,[vp,vp,vp])
    getdest=fn('?getAllDesPin@Net@NETLIST@@QEAAAEBV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp])
    def vector(p):
        first,last,cap=(ptr(p+i) for i in (0,8,16))
        if not first:
            if last or cap:raise ValueError('Malformed empty vector')
            return []
        if not first<=last<=cap or (last-first)%8 or (last-first)//8>1000000:raise ValueError('Invalid native vector')
        return [ptr(a) for a in range(first,last,8)]
    def points(pin):
        if not pin:return []
        v=(C.c_uint64*3)();pin_nodes(pin,C.addressof(v),mem);return vector(C.addressof(v))
    def snapshot():
        v=(C.c_uint64*3)();fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(v))
        geometry={};instances=vector(C.addressof(v))
        for obj in instances:
            b=C.create_string_buffer(32);getname(obj,C.addressof(b));p=C.addressof(b);length=ptr(p+16)
            if length>4096:raise ValueError('Invalid instance name')
            name=C.string_at(ptr(p) if ptr(p+24)>=16 else p,length).decode();physical=getphy(obj)
            geometry[str(obj)]=dict(name=name,physical_header=C.string_at(physical+0x10,8).hex() if physical else None)
        nets=fn('?getAllNetVec@Design@NETLIST@@QEAAAEAV?$vector@PEAVNet@NETLIST@@V?$allocator@PEAVNet@NETLIST@@@std@@@std@@XZ',vp,[vp])(design)
        links={};nodes={};status={};net_count=0;pin_records={}
        for net in vector(nets):
            net_count+=1;driver=ptr(net+0x18);dest=vector(getdest(net));links[str(net)]=[driver,dest]
            for pin in ([driver] if driver else [])+dest:
                endpoints=points(pin)
                if request.get('capture_connectivity') and str(pin) not in pin_records:
                    owner=ptr(pin+0x10);b=C.create_string_buffer(32)
                    fn('?getPinNameForRpt@Pin@NETLIST@@QEAAXAEAV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z',None,[vp,vp])(pin,C.addressof(b))
                    bp=C.addressof(b);length=ptr(bp+16)
                    if length>4096:raise ValueError('Invalid pin label')
                    label=C.string_at(ptr(bp) if ptr(bp+24)>=16 else bp,length).decode()
                    vt=ptr(owner);locator=ptr(vt-8);td=C.c_uint32.from_address(locator+12).value
                    kind=C.string_at(dll._handle+td+16).decode('ascii')
                    pin_records[str(pin)]=dict(owner=owner,label=label,logical_class=kind,nodes=endpoints,net=ptr(pin+8),inverted=bool(fn('?hasInvertInPin@Pin@NETLIST@@QEAA_NXZ',C.c_bool,[vp])(pin)))
                for node in endpoints:
                    seen=set()
                    for _ in range(20000):
                        if not node:break
                        if node in seen:raise ValueError('Cyclic native physical parent')
                        seen.add(node)
                        if str(node) in nodes:break
                        parent=ptr(node+0x18)
                        nodes[str(node)]=[C.string_at(node,6).hex(),parent,C.c_ubyte.from_address(node+0x2a).value]
                        node=parent
                    else:raise ValueError('Native parent trace limit')
        from native_io_state import capture
        from types import SimpleNamespace
        chip=fn('?getDevChip@globalMem@@QEAAPEAVchip@NETLIST@@XZ',vp,[vp])(mem)
        io=capture(SimpleNamespace(fn=fn,ptr=ptr,mem=mem,chip=chip))
        banks={str(bank):fn('?getBankVccio@chip@NETLIST@@QEAA?AW4PIO_VALUE@2@F@Z',C.c_int,[vp,C.c_short])(chip,bank) for bank in range(16)}
        return dict(geometry=geometry,instance_vector_entries=len(instances),logical_net_connections=links,routed_endpoint_parent_nodes=nodes,logical_pin_nodes=pin_records,io=io,bank_vccio=banks,
            net_count=net_count,scope='All native design net-vector entries and their physical endpoint ancestors; unused/default routing resources excluded.')
    request=json.loads((out/'request.json').read_text())
    normalization=None
    if request.get('restore_native_inverters'):
        pre=snapshot()
        from native_inverter_normalization import normalize
        normalization=normalize(fn,mem,request.get('normalization_engine','stock'));post=snapshot()
        physical=lambda r:{k:v for k,v in r['geometry'].items() if v['physical_header'] is not None}
        checks=dict(physical_instance_headers=physical(pre)==physical(post),routed_parent_nodes=pre['routed_endpoint_parent_nodes']==post['routed_endpoint_parent_nodes'],io=pre['io']==post['io'],bank_vccio=pre['bank_vccio']==post['bank_vccio'])
        normalization['physical_state_checks']=checks
        (out/'inverter_normalization.json').write_text(json.dumps(normalization,indent=2))
        if not all(checks.values()):raise ValueError('Native inverter normalization changed physical state')
        if normalization['errors'] and not request.get('observe_normalization'):raise ValueError('Native inverter normalization failed semantic invariants')
    source=(out/'initial_clock.sdc').read_text();initial_period=float(re.search(r'create_clock\s+-name\s+clk\s+-period\s+([\d.]+)',source)[1])
    initial_report=(out/'probe.tr').read_text();initial=parse(initial_report,source,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':initial_period}))
    def analysis_counts(text):
        counts={}
        for name in ('Paths','Endpoints','Falling Endpoints'):
            suffix=' Analyzed' if name!='Falling Endpoints' else ''
            match=re.search(r'<Numbers of '+name+suffix+r'>:\s*(\d+)',text)
            if not match:raise ValueError('Missing full analysis inventory '+name)
            counts[name]=int(match[1])
        return counts
    initial_counts=analysis_counts(initial_report)
    shutil.copyfile(out/'probe.tr',out/'initial.tr')
    before=snapshot();(out/'physical_before.json').write_text(json.dumps(before,separators=(',',':')))
    result=dict(status='calibrating',official_pnr_executions=1,own_pnr_result=False,baseline_frozen=False,phases=[],initial_frequency_reports=initial['frequency_reports'],initial_hold_violations=initial['violated_endpoints']['Hold'],initial_monitor_flags=C.string_at(monitor+0x10,16).hex(),initial_context_flags=C.string_at(ptr(monitor+8)+0x200,0x30).hex(),initial_library_selector=C.c_int.from_address(ptr(ptr(monitor+8)+0x38)+0x18).value,native_verilog_mode=bool(fn('?getVlgFlag@globalMem@@QEAA_NXZ',C.c_bool,[vp])(mem)))
    def save():(out/'reload_result.json').write_text(json.dumps(result,indent=2))
    save()
    if normalization:
        result.update(inverter_normalization_sha256=sha(out/'inverter_normalization.json'),normalization_semantics_verified=not normalization['errors'],observation_only=bool(normalization['errors']));save()
    if request.get('probe_stages'):
        rows=[];result['stage_isolation']=rows
        for label in ('regenerate_only','evaluate_only','restart_only','read_sdc_only'):
            if label=='evaluate_only':C.CFUNCTYPE(C.c_bool,vp)(dll._handle+0x13b77b0)(monitor)
            if label=='restart_only':
                fn('?restartTiming@TmMonitor@STA@@QEAAXXZ',None,[vp])(monitor)
                C.CFUNCTYPE(C.c_bool,vp)(dll._handle+0x13b77b0)(monitor)
            if label=='read_sdc_only':
                if fn('?readSdcFile@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])(mem):raise ValueError('Isolated SDC reread failed')
            if fn('?generateTimingRptoutFile@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])(mem):raise ValueError('Isolated report generation failed')
            report_text=(out/'probe.tr').read_text();shutil.copyfile(out/'probe.tr',out/(label+'.tr'))
            detail={}
            for mode in ('setup','hold','recovery','removal'):
                old,new=envelopes(initial_report,mode),envelopes(report_text,mode)
                comparable=old.keys()<=new.keys()
                detail[mode]=dict(initial_paths=len(old),new_paths=len(new),original_endpoints_preserved=comparable,
                    max_error_ns=max([0.]+[abs(a-b) for key in old.keys()&new.keys() for a,b in zip(old[key],new[key])]))
            rows.append(dict(stage=label,physical_state_unchanged=snapshot()==before,coverage=detail));save()
        result.update(status='stage_isolation_completed_no_baseline_acceptance');save();return
    def reload(label,text,period):
        nonlocal monitor
        (out/'probe.sdc').write_text(text)
        code=fn('?readSdcFile@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])(mem)
        if code:raise ValueError('Native SDC reload failed '+str(code))
        if request['reload_engine']=='fresh_monitor':
            fn('?freeTiming@globalMem@@QEAAXXZ',None,[vp])(mem)
            monitor=fn('?getTmMonitor@globalMem@@QEAAPEAVTmMonitor@STA@@XZ',vp,[vp])(mem)
            if not monitor or not fn('?setTimingData@@YA_NPEAVglobalMem@@@Z',C.c_bool,[vp])(mem):raise ValueError('Fresh timing monitor initialization failed')
        if request['rebind_register_pins']:
            reg_names={r['name'] for r in json.loads((out/'primitive_bindings.json').read_text()) if r['logical_class']=='.?AVReg@NETLIST@@'}
            bindreg=fn('?setPin2Node@Reg@NETLIST@@UEAAXXZ',None,[vp])
            for address,entry in before['geometry'].items():
                if entry['name'] in reg_names:bindreg(int(address))
        # P&R has already loaded the cell/timing library. Re-running
        # setTimingData attempts to reread its one-shot stream and fails;
        # retain that library and rebuild constraints/propagation below.
        if request['report_flow_policy']=='native_final':
            fn('?setFlowFlag@TmMonitor@STA@@QEAAXW4FLOW_FLAG_STA@2@@Z',None,[vp,C.c_int])(monitor,7)
            fn('?setPlaceFlag@TmMonitor@STA@@QEAAX_N@Z',None,[vp,C.c_bool])(monitor,False)
            C.c_ubyte.from_address(monitor+0x12).value=0
        if request['reload_engine']=='clock_in_place':
            # Constructor 0x13b8140 and reference-clock getter 0x138eac0
            # establish these fields for the pinned DLL. Only one ordinary
            # zero-rise, half-period-fall primary clock is admitted.
            clocks=vector(ptr(monitor+8)+0x78);found=[]
            for clock in clocks:
                p=clock+0x30;length=ptr(p+16)
                if length>4096:raise ValueError('Invalid clock name')
                name=C.string_at(ptr(p) if ptr(p+24)>=16 else p,length).decode()
                if name=='clk':found.append(clock)
            if len(found)!=1:raise ValueError('Expected one native primary clock object')
            clock=found[0];edges=ptr(clock+0x58);old=C.c_float.from_address(clock+0x50).value
            if ptr(clock+0x78) or not edges or C.c_ubyte.from_address(clock+0x9c).value:raise ValueError('Generated/wrapped clock is unsupported')
            rise=C.c_float.from_address(edges+0xc).value;fall=C.c_float.from_address(edges+0x4c).value
            if abs(rise)>1e-6 or abs(fall-old/2)>1e-5:raise ValueError('Nonstandard primary waveform')
            if ptr(edges)!=clock or ptr(edges+0x40)!=clock:raise ValueError('Clock edge ownership differs')
            C.c_float.from_address(clock+0x50).value=period
            C.c_float.from_address(edges+0x4c).value=period/2
            if request['rebuild_clock_relations']:
                if not C.CFUNCTYPE(C.c_bool,vp)(dll._handle+0x12e1670)(ptr(monitor+8)):raise ValueError('Clock relation rebuild failed')
            fn('?incUpdateTiming@TmMonitor@STA@@QEAAXXZ',None,[vp])(monitor)
            result.setdefault('clock_constraint_edits',[]).append(dict(label=label,old_period_ns=old,new_period_ns=period,
                fields=['primary_clock.period_float','primary_clock.falling_edge_time_float'],rising_edge_unchanged=True,
                evidence=['abi/clock_constructor.asm','abi/ref_clock_pin.asm']))
        elif request['reload_engine']=='constraints_only':
            context=ptr(monitor+8)
            for rva in (0x13b6010,0x12e1670):
                if not C.CFUNCTYPE(C.c_bool,vp)(dll._handle+rva)(context):raise ValueError('Constraint refresh failed')
        elif request['reload_engine'] in ('restart','fresh_monitor'):
            if request['restart_selector'] is None:fn('?restartTiming@TmMonitor@STA@@QEAAXXZ',None,[vp])(monitor)
            else:fn('?restartTiming@TmMonitor@STA@@QEAAXI@Z',None,[vp,C.c_uint])(monitor,request['restart_selector'])
        recomputed=bool(C.CFUNCTYPE(C.c_bool,vp)(dll._handle+0x13b77b0)(monitor))
        if fn('?generateTimingRptoutFile@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])(mem):raise ValueError('Native final timing report failed')
        text_report=(out/'probe.tr').read_text();shutil.copyfile(out/'probe.tr',out/(label+'.tr'));(out/(label+'.sdc')).write_text(text)
        counts=analysis_counts(text_report)
        if counts!=initial_counts:raise ValueError('Native full analysis inventory changed: '+str(initial_counts)+' -> '+str(counts))
        report=parse(text_report,text,dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns={'clk':period}))
        after=snapshot();unchanged=after==before
        phase=dict(label=label,sta_recompute_return=recomputed,period_ns=period,physical_state_unchanged=unchanged,
            monitor_flags=C.string_at(monitor+0x10,16).hex(),context_flags=C.string_at(ptr(monitor+8)+0x200,0x30).hex(),
            frequency_reports=report['frequency_reports'],violated_endpoints=report['violated_endpoints'],worst_reported_slack=report['worst_reported_slack_by_capture_clock'])
        result['phases'].append(phase);save()
        if not unchanged:
            (out/(label+'_physical_after.json')).write_text(json.dumps(after,separators=(',',':')))
            raise ValueError('SDC-only STA changed logical/physical state')
        return report,text_report
    expanded_source=source+''.join(f'\nreport_timing -{kind} -max_paths 10000 -max_common_paths 1' for kind in ('setup','hold','recovery','removal'))+'\nreport_min_pulse_width -nworst 10000 -detail\n'
    expanded,expanded_text=reload('expanded_initial',expanded_source,initial_period)
    result['expanded_initial_validation']=compare_envelopes({m:envelopes(initial_report,m) for m in MODES},{m:envelopes(expanded_text,m) for m in MODES},allow_expansion=True)
    initial_report=expanded_text;source=expanded_source
    same,same_text=reload('same_period',source,initial_period)
    same_checks=compare_envelopes({m:envelopes(initial_report,m) for m in MODES},{m:envelopes(same_text,m) for m in MODES})
    errors={m:max(r['maximum_errors_ns'].values()) for m,r in same_checks.items()}
    result['same_period_validation']=same_checks
    result['same_period_maximum_envelope_error_ns']=errors;save()
    baseline=initial['frequency_reports']['clk']['reported_fmax_mhz'];evaluation=target_evaluation_mhz(baseline);period=1000/evaluation
    final,final_text=reload('target_relative',clock_period(source,period),period)
    from ownpr_effective_io_constraints import audit as effective_io
    invariant_checks=compare_envelopes({m:envelopes(same_text,m) for m in MODES},{m:envelopes(final_text,m) for m in MODES},period_delta_ns=period-initial_period)
    result['changed_period_invariants']=invariant_checks
    result['effective_io_delays']=effective_io(final_text,clock_period(source,period))
    result['physical_snapshot_counts']=dict(instances=len(before['geometry']),nets=before['net_count'],routed_parent_nodes=len(before['routed_endpoint_parent_nodes']),io=len(before['io']['io']))
    if request.get('capture_ioreg'):
        from native_ioreg_state import capture as capture_ioreg
        from types import SimpleNamespace
        chip=fn('?getDevChip@globalMem@@QEAAPEAVchip@NETLIST@@XZ',vp,[vp])(mem)
        state=capture_ioreg(SimpleNamespace(fn=fn,ptr=ptr,dll=dll,mem=mem,chip=chip))
        (out/'ioreg_state.json').write_text(json.dumps(state,indent=2))
        if snapshot()!=before:raise ValueError('I/O-register observation changed physical/logical state')
        result['ioreg_state_sha256']=sha(out/'ioreg_state.json')
    result.update(status='timing_observation_only_semantic_proof_failed' if normalization and normalization['errors'] else 'fixed_layout_sta_calibrated',provisional_reference_mhz=baseline,sta_evaluation_mhz=evaluation,
        final_reported_fmax_mhz=final['frequency_reports']['clk']['reported_fmax_mhz'],
        final_hold_passed=final['violated_endpoints']['Hold']==0,
        full_fair_baseline_accepted=False,scope='Same-period and target-relative routed STA on retained official layout; source equivalence, full I/O/physical/configuration baseline acceptance remains separate.')
    save();print('FIXED_LAYOUT_STA_RESULT',json.dumps(result),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('out','netlist','cst','sdc','budget'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--initial-period',type=float,default=10.)
    p.add_argument('--device-config',type=Path,help='Exact official device.cfg; preserve its encoding/default policy instead of bare DLL constructor defaults')
    p.add_argument('--native-command-template',type=Path,help='Exact official generated cmd.do; source inputs and non-path options are verified and preserved')
    p.add_argument('--bitstream',action='store_true',help='Include native encoding before retained-state calibration; capture final physical I/O attributes')
    p.add_argument('--capture-connectivity',action='store_true',help='Capture post-normalization logical pin identities and all returned physical endpoint nodes')
    p.add_argument('--capture-ioreg',action='store_true',help='Observe typed I/O-register bindings, controls and native direct edges after final STA')
    p.add_argument('--io-register-policy',choices=('native_defaults','core_inputs','core_all'),default='native_defaults',help='Uniform calibration policy; core_inputs keeps input/inout FFs in fabric, core_all also keeps outputs in fabric as the official GUI default')
    p.add_argument('--report-flow-policy',choices=('native_final','preserve'),default='native_final',help='Calibrate report mode separately; preserve keeps the post-P&R monitor mode')
    p.add_argument('--restart-selector',type=int,choices=(0,1,2),help='Hash-pinned overloaded restartTiming selector; calibration only, all four timing classes must match')
    p.add_argument('--probe-stages',action='store_true',help='Isolate post-task regeneration, propagation, restart and SDC reread without accepting a baseline')
    p.add_argument('--reload-engine',choices=('restart','evaluate_only','constraints_only','clock_in_place','fresh_monitor'),default='restart',help='Isolated calibration alternatives; every timing class must preserve witnesses')
    p.add_argument('--rebuild-clock-relations',action='store_true',help='Calibrate rebuilding cached clock relationships without deleting clock objects')
    p.add_argument('--rebind-register-pins',action='store_true',help='Calibrate restoring native logical-to-physical register pin bindings before rebuilding STA')
    p.add_argument('--omit-verilog-output',action='store_true',help='Do not request post-P&R Verilog, which may restore merged virtual inverters in retained native state')
    p.add_argument('--restore-native-inverters',action='store_true',help='Calibrate replaying the stock inverter-fold pass, with local semantic and full physical invariants')
    p.add_argument('--normalization-engine',choices=('stock','recorded_controls'),default='stock',help='Recorded controls uses exported netlist mutators only after all fanouts pass strict preflight')
    p.add_argument('--observe-normalization',action='store_true',help='Diagnostic only: collect timing despite unresolved annotation semantics; never a valid baseline')
    p.add_argument('--worker',action='store_true');a=p.parse_args();a.out=a.out.resolve()
    if a.worker:return worker(a.out)
    if a.device_config and a.native_command_template:p.error('Command template already supplies device.cfg')
    reserve(ROOT,min(600_000_000,200_000_000+a.netlist.stat().st_size*200));budget=json.loads(a.budget.read_text());remaining=budget['deadline_epoch']-time.time()
    if remaining<min(400,120+a.netlist.stat().st_size*.0002):raise TimeoutError('Original complete trial deadline')
    prepare(a.out)
    shutil.copyfile(a.netlist,a.out/'probe.vg');shutil.copyfile(a.cst,a.out/'project.cst')
    sdc=clock_period(a.sdc.read_text(),a.initial_period)
    (a.out/'probe.sdc').write_text(sdc);(a.out/'initial_clock.sdc').write_text(sdc)
    (a.out/'request.json').write_text(json.dumps(dict(report_flow_policy=a.report_flow_policy,restart_selector=a.restart_selector,probe_stages=a.probe_stages,reload_engine=a.reload_engine,rebuild_clock_relations=a.rebuild_clock_relations,rebind_register_pins=a.rebind_register_pins,restore_native_inverters=a.restore_native_inverters,normalization_engine=a.normalization_engine,observe_normalization=a.observe_normalization,capture_connectivity=a.capture_connectivity,capture_ioreg=a.capture_ioreg),indent=2))
    lines=(a.out/'cmd.do').read_text().splitlines();output=[line for line in lines if line.startswith('-o ')]
    if a.omit_verilog_output:output=[]
    lines=[line for line in lines if line not in ('-ireg_not_in_iob','-oreg_not_in_iob','-ioreg_not_in_iob') and not line.startswith('-o ')]
    if a.io_register_policy in ('core_inputs','core_all'):lines+=['-ireg_not_in_iob','-ioreg_not_in_iob']
    if a.io_register_policy=='core_all':lines+=['-oreg_not_in_iob']
    if a.bitstream:lines+=['-bit']
    if a.device_config:
        shutil.copyfile(a.device_config,a.out/'device.cfg')
        lines+=['-cfg '+(a.out/'device.cfg').as_posix()]
    cmd='\n'.join(lines+['-correct_hold 1','-route_maxfan 23','-cst '+(a.out/'project.cst').as_posix(),*output])+'\n';(a.out/'cmd.do').write_text(cmd)
    template_info=None
    if a.native_command_template:
        from official_command_template import rebind
        cmd,template_info=rebind(a.native_command_template,a.out,a.netlist,a.cst,a.sdc,a.io_register_policy,a.bitstream)
        (a.out/'cmd.do').write_text(cmd);(a.out/'command_template_audit.json').write_text(json.dumps(template_info,indent=2))
    shutil.copyfile(__file__,a.out/'runner_source.py.txt')
    shutil.copyfile(ROOT/'scripts/ownpr_sta_invariants.py',a.out/'invariant_helper_source.py.txt')
    if a.restore_native_inverters:
        shutil.copyfile(ROOT/'scripts/native_inverter_normalization.py',a.out/'inverter_helper_source.py.txt')
        shutil.copyfile(ROOT/'scripts/ownpr_inverter_view_proof.py',a.out/'inverter_view_proof_source.py.txt')
    runtime=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.time()
    command=[str(runtime),str(Path(__file__).resolve()),*sys.argv[1:],'--worker']
    record=dict(status='running',role='official_fixed_layout_sta_calibration',own_pnr_result=False,io_register_policy=a.io_register_policy,source_netlist_sha256=sha(a.netlist),cst_sha256=sha(a.cst),source_sdc_sha256=sha(a.sdc),original_budget=budget,command=command)
    record['bitstream_requested']=a.bitstream
    if a.device_config:record.update(source_device_config=str(a.device_config.resolve()),device_config_sha256=sha(a.device_config))
    if template_info:record.update(command_template=template_info,command_template_audit_sha256=sha(a.out/'command_template_audit.json'))
    def save():(a.out/'manifest.json').write_text(json.dumps(record,indent=2))
    save()
    with (a.out/'worker.log').open('x') as log:
        proc=subprocess.Popen(command,cwd=ROOT,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:code=proc.wait(timeout=min(500,remaining-30))
        except subprocess.TimeoutExpired:stop_owned_process_tree(proc);record.update(status='timeout');save();raise
    worker_result=json.loads((a.out/'reload_result.json').read_text()) if (a.out/'reload_result.json').exists() else {}
    record.update(status=worker_result.get('status','worker_completed_without_calibration') if code==0 else 'failed',returncode=code,wall_seconds=time.time()-start,within_original_deadline=time.time()<budget['deadline_epoch'],input_copy_unchanged=sha(a.out/'probe.vg')==sha(a.netlist),cst_copy_unchanged=sha(a.out/'project.cst')==sha(a.cst));save()
    print(json.dumps(record,indent=2))
    if code:raise SystemExit(1)


if __name__=='__main__':main()
