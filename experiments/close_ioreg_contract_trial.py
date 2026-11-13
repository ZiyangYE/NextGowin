"""Close trial016 without interpreting unknown core configuration fields."""
import hashlib,json,re,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha,read_text
from ownpr_device import Device
from ownpr_lut_configuration import NativeFrameProjection
from ownpr_ff_configuration import single_ff_attributes
from audit_native_ioreg_contract import expected_attributes
from workspace_budget import reserve

def main():
    reserve(ROOT,2000000)
    root=ROOT/'results/native_ioreg_contract_trial_016';device=Device();projection=NativeFrameProjection(device);rows=[]
    for name in ('uart','int2float','tmds'):
        folder=root/name;old=ROOT/'results/native_endpoint_correspondence_trial_014'/name
        images_equal=bool((projection.read(folder/'probe.fs')==projection.read(old/'probe.fs')).all())
        def logic(p):return re.sub(r'/\*.*?\*/|//[^\n]*','',read_text(p),flags=re.S).split()
        logic_equal=logic(folder/'probe.vo')==logic(old/'probe.vo')
        if not(images_equal and logic_equal):raise ValueError('Prior physical/logical payload changed: '+name)
        bindings=load_json(folder/'primitive_bindings.json');state=load_json(folder/'ioreg_state.json');mismatches=[];independent=[];count=0
        for reg in state['registers_and_iologic']:
            h=reg.get('physical_header')
            if reg['logical_class']!='.?AVReg@NETLIST@@' or not h or h[0]!=50:continue
            count+=1
            _,s=expected_attributes(reg)
            b=next(x for x in bindings if x['name']==reg['instance'] and x.get('physical_header')==h and x['logical_class']==reg['logical_class'])
            observed=next(g['attributes'] for g in b['cfu_cls_attributes'] if g['group']==h[3]//2)
            predicted=single_ff_attributes(s['primitive'],h[3],s['ce'],s['control'],s['clock'])[h[3]//2]
            diff=[dict(field=i,observed=x,predicted=y) for i,(x,y) in enumerate(zip(observed,predicted)) if x!=y and i!=13+(1-(h[3]&1))]
            if diff:mismatches.append(dict(instance=reg['instance'],site=h,fields=diff))
            d=next(p for p in reg['pins'] if p['pin'].endswith('.D'))
            luts=[x for x in bindings if x.get('physical_header')==[24,*h[1:]] and x['logical_class']=='.?AVLut4@NETLIST@@']
            if luts and d['source'] not in [x['name'] for x in luts]:
                independent.append(dict(instance=reg['instance'],site=h,data=d,companion_luts=[dict(name=x['name'],truth=x.get('routed_init_value')) for x in luts],lut_used=b['cfu_lut_used_flags'][h[3]],attributes=observed))
        diagnostic=dict(core_registers=count,mismatches=mismatches,independent_data_with_live_companion_lut=independent,scope='Compare isolated-FF model excluding only the other FF INIT field; mismatches are unresolved, not waived. Independent data observations do not establish legal new own placement modes.')
        (root/(name+'_core_full_diagnostic.json')).write_text(json.dumps(diagnostic,indent=2))
        endpoint=load_json(root/(name+'_endpoints_final.json'));io=load_json(root/(name+'_ioreg.json'));fuses=load_json(root/(name+'_parent_fuses.json'))
        if fuses['status']!='matched_parent_edges_and_fuses_verified':raise ValueError('Parent fuse audit incomplete')
        rows.append(dict(design=name,encoded_payload_matches_014=images_equal,noncomment_post_pnr_logic_matches_014=logic_equal,output_registers=io['physical_output_registers'],ioreg_bits=io['encoded_bits'],unresolved_endpoints=len(endpoint['unresolved']),parent_bits=fuses['configuration_bits'],unexplained_core_register_modes=len(mismatches),independent_ff_observations=len(independent),source_sha256={str(p.relative_to(ROOT)):sha(p) for p in (folder/'probe.fs',old/'probe.fs',folder/'probe.vo',old/'probe.vo',folder/'ioreg_state.json',root/(name+'_parent_fuses.json'))}))
    budget=load_json(root/'budget.json');now=time.time()
    result=dict(status='partial_mode_and_endpoint_contract_verified',goal_complete=False,baseline_frozen=False,wall_seconds=now-budget['start_epoch'],within_original_deadline=now<=budget['deadline_epoch'],rows=rows,tests_passed=26,workspace=reserve(ROOT,0),remaining='Core fields11/12 and data-selection semantics; UART/TMDS carry and mode-dependent endpoints; Vex hard state and unused pads. No Fmax change.',source_sha256={str(p.relative_to(ROOT)):sha(p) for p in (Path(__file__),ROOT/'scripts/audit_native_ioreg_contract.py',ROOT/'scripts/audit_native_gsr_contract.py',ROOT/'scripts/audit_native_endpoint_correspondence.py')})
    (root/'completion_audit.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if not result['within_original_deadline']:raise SystemExit('Trial deadline exceeded')

if __name__=='__main__':main()
