"""Independent fixed-I/O placement of arithmetic chains and surrounding logic."""
import argparse,json,time
from pathlib import Path
from ownpr_device import Device,ROOT
from ownpr_place import pack,place,constraints,sha
from ownpr_carry_macros import contract as carry_contract,validate as validate_carry
from ownpr_validate import placement as validate_logic
from native_boundary_cec import prove


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--netlist',type=Path,required=True);ap.add_argument('--cst',type=Path,required=True);ap.add_argument('--sdc',type=Path,required=True)
    ap.add_argument('--clock-field',type=Path);ap.add_argument('--max-clock-wire',type=float)
    ap.add_argument('--timing-feedback',type=Path)
    ap.add_argument('--pin-feedback',type=Path);ap.add_argument('--critical-window-ns',type=float,default=4.)
    ap.add_argument('--anchor-placement',type=Path);ap.add_argument('--anchor-sta',type=Path)
    ap.add_argument('--wide',action='store_true',help='Pack exclusive calibrated LUT5/6/7 macros during initial placement')
    ap.add_argument('--hard',action='store_true',help='Experimental calibrated BRAM/DSP capacity placement; routing/export gates remain separate')
    ap.add_argument('--top',default='top');ap.add_argument('--out',type=Path,required=True);ap.add_argument('--runtime',type=Path,default=ROOT/'results/native_route_runtime_007');a=ap.parse_args();out=a.out.resolve();out.mkdir(exist_ok=False);start=time.monotonic()
    from workspace_budget import reserve
    reserve(ROOT,500_000_000)
    original=json.loads(a.netlist.read_text());external=constraints(original['modules'][a.top],a.cst.read_text(),a.sdc.read_text());device=Device()
    design,groups,constants,packing=pack(original,a.top,allow_alu=True,allow_wide=a.wide,allow_hard=a.hard);carry=carry_contract(design,device,a.top)
    hard=None;hard_model=None
    if a.hard:
        from ownpr_hard_architecture import HardArchitecture
        hard_model=HardArchitecture();hard=hard_model.contract(design,a.top)
    wide=[]
    if a.wide:
        from ownpr_mux_macros import tree,validate as validate_wide
        cells=design['modules'][a.top]['cells'];drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs};muxes={n for n,c in cells.items() if c['type'].startswith('MUX2_LUT')}
        nested={drivers[cells[n]['connections'][p][0]] for n in muxes for p in ('I0','I1')}&muxes
        wide=[tree({'modules':{'top':design['modules'][a.top]}},n) for n in sorted(muxes-nested)]
        if set(n for spec in wide for n in spec['muxes'])!=muxes:raise ValueError('Incomplete/cyclic wide macro inventory')
    clock_sites=None;clock_info=None
    if a.clock_field:
        from ownpr_device import DLL_SHA
        if external['clock_routing']:raise ValueError('Dedicated clock-placement field requires the existing AUTO clock contract')
        meta=json.loads((a.clock_field/'manifest.json').read_text());field=json.loads((a.clock_field/'clock_field.json').read_text())
        if meta['status']!='measured' or meta['dll_sha256']!=DLL_SHA or meta['field_sha256']!=sha(a.clock_field/'clock_field.json') or a.max_clock_wire is None or a.max_clock_wire<=0:raise ValueError('Invalid clock-placement field/budget')
        clock_sites={(r['node'][0]-1,r['node'][1]-1) for r in field['sites'] if r['setup_wire_ns']<=a.max_clock_wire}
        clock_info=dict(field_sha256=meta['field_sha256'],maximum_clk0_wire_ns=a.max_clock_wire,eligible_sites=len(clock_sites),actual_merged_clock_tree_verified=False)
    elif a.max_clock_wire is not None:raise ValueError('Clock insertion budget requires a measured field')
    critical_endpoints=None;feedback_info=None
    if a.pin_feedback and not a.timing_feedback:raise ValueError('Pin feedback requires its audited STA source')
    if a.timing_feedback:
        from ownpr_placement_feedback import load
        feedback_meta=json.loads((a.timing_feedback/'manifest.json').read_text())
        if feedback_meta['cst_sha256']!=sha(a.cst) or feedback_meta['source_sdc_sha256']!=sha(a.sdc):raise ValueError('Placement feedback uses different fixed I/O or timing constraints')
        critical_endpoints,feedback_info=load(a.timing_feedback,design,a.critical_window_ns if a.pin_feedback else None,a.pin_feedback)
    projected=design
    if packing['materialized_unused_inputs']:
        from ownpr_hard_architecture import unused_pad_projection
        projected=unused_pad_projection(original,design,packing['materialized_unused_inputs'],a.top)
    proof=prove(original,projected,out/'packing_cec',120)
    if packing['materialized_unused_inputs']:packing['unused_input_buffers_proven_unobservable']=True
    ff_sites=None;anchor_info=None
    if bool(a.anchor_placement)!=bool(a.anchor_sta):raise ValueError('Both anchor placement and audited STA are required')
    if a.anchor_placement:
        from audit_ownpr_native_export import audit as audit_export
        meta=json.loads((a.anchor_sta/'manifest.json').read_text())
        if meta['cst_sha256']!=sha(a.cst) or meta['source_sdc_sha256']!=sha(a.sdc):raise ValueError('Fixed FF anchors use different constraints')
        audit_export(a.anchor_sta,a.anchor_placement);prior=json.loads((a.anchor_placement/'packed.json').read_text());layout=json.loads((a.anchor_placement/'placement.json').read_text())
        equivalence=prove(prior,design,out/'anchor_cec',120)
        ff_sites={r['ff']:[r['row'],r['column'],r['slot']] for r in layout['clusters'].values() if r.get('ff')}
        anchor_info=dict(placement_sha256=sha(a.anchor_placement/'placement.json'),netlist_sha256=sha(a.anchor_placement/'packed.json'),sta_sha256=sha(a.anchor_sta/'handoff_audit.json'),ff_count=len(ff_sites),proof=equivalence)
    placed=place(design,a.top,groups,constants,external,device,a.runtime,carry,clock_sites,critical_endpoints,wide,hard,ff_sites)
    hard_check=hard_model.validate(design,placed,device,a.top) if hard_model else None
    if hard_check:(out/'hard_placement_audit.json').write_text(json.dumps(hard_check,indent=2))
    if a.wide:(out/'wide_placement_audit.json').write_text(json.dumps(validate_wide({'modules':{'top':design['modules'][a.top]}},placed,device),indent=2))
    if feedback_info:placed['timing_placement_feedback']=feedback_info
    if a.clock_field:
        roots=[r['native_ports']['out'] for r in placed['fixed_io'].values() if r['port']==external['clock_port']]
        if roots!=[field['source']]:raise ValueError('Clock placement field uses a different fixed clock source')
        placed['clock_placement_guidance']=clock_info
    native_grid=json.loads((a.runtime/'native_grid_types.json').read_text())['grid'];native_io=json.loads((a.runtime/'native_iob_ports.json').read_text())
    logic_check=validate_logic(design,placed,native_grid,native_io,device.pin_site_name,external,a.top);carry_check=validate_carry(design,placed,device,a.top)
    for name,data in [('packed.json',design),('placement.json',placed),('carry_contract.json',carry),('carry_placement_audit.json',carry_check),('logic_placement_audit.json',logic_check)]:
        (out/name).write_text(json.dumps(data,indent=2))
    result=dict(status='placed',top=a.top,source_sha256=sha(a.netlist),netlist_sha256=sha(out/'packed.json'),placement_sha256=sha(out/'placement.json'),cst_sha256=sha(a.cst),sdc_sha256=sha(a.sdc),
        cells=len(design['modules'][a.top]['cells']),ordinary_clusters=len(groups),alu_cells=len(placed['arithmetic_clusters']),carry_chains=len(carry['chains']),reserved_cfus=carry_check['reserved_cfus'],
        packing=packing,proof=proof,clock_placement_guidance=clock_info,timing_placement_feedback=feedback_info,wall_seconds=time.monotonic()-start,vendor_pnr_executed=False,open_chip_database_used=False,
        hard_resources=hard_check,fixed_ff_anchors=anchor_info,
        all_ports_fixed=True,timing_signoff=False,physical_legality_complete=False,bitstream_generated=False,
        scope='Own analytical placement and six-slot/eastward carry macro legalizer; independent capacity/pattern checks, not complete carry timing or hardware signoff')
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k not in ('proof','packing')},indent=2))


if __name__=='__main__':main()
