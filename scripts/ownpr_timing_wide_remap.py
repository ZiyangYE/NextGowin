"""Routed-STA-selected two-LUT resynthesis into a calibrated LUT5/6 macro.

Use the actual preceding path input as the final dedicated MUX selector.
The free-CFU placement is a proposal; CEC and full own P&R decide acceptance.
"""
import argparse,copy,json,shutil,sys,time
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from ownpr_critical_tree_rebuild import verify_feedback,critical_labels
from ownpr_critical_chain_move import exclusive_tiles,apply as validate_placement
from ownpr_wide_cone import replace_pair
from reorder_ownpr_wide_inputs import reorder
from ownpr_mux_macros import tree
from native_boundary_cec import prove
from workspace_budget import reserve


def late_boundary(design,parent,root,traces,slack):
    cells=design['modules']['top']['cells'];choices=[]
    for path in traces:
        if path['slack_ns']>slack+1e-9:continue
        previous=None
        for arc in path['ordinary_wires']:
            name,pin=arc['endpoint'].rsplit('/',1)
            if name==parent:previous=cells[parent]['connections'][pin][0]
            if name==root and previous is not None:choices.append(previous)
    if not choices:raise ValueError('No preceding boundary input on critical path')
    # Multiple tied critical transitions may enter different pins. Keep a
    # deterministic candidate; all other inputs remain in full STA acceptance.
    return min(choices)


def transform(design,parent,root,late_bit):
    after,change=replace_pair(design,parent,root)
    if change['cut_width'] not in (5,6):raise ValueError('Only calibrated full-design LUT5/6 cuts are admitted')
    if late_bit not in change['cut_bits']:raise ValueError('Critical input is not a wide-cut boundary')
    if after['modules']['top']['cells'][root]['connections']['S0']!=[late_bit]:after,promotion=reorder(after,root,late_bit)
    else:promotion=dict(promoted_signal=late_bit,target_level=change['cut_width'],already_at_final_selector=True)
    return after,dict(**change,promotion=promotion,lut_growth=(1<<(change['cut_width']-4))-1)


def place(design,original,root,device,allow_paired=False,site_cost=None,share_ordinary=False):
    old=original['clusters'][root]
    ff=old.get('ff')
    if (ff and not allow_paired) or (old['row'],old['column']) in exclusive_tiles(original):raise ValueError('Root occupies a state pair or exclusive macro')
    result=copy.deepcopy(original);del result['clusters'][root];spec=tree(design,root)
    if share_ordinary and (ff or spec['level'] not in (5,6)):raise ValueError('Shared ordinary LUTs require an unpaired LUT5/6 root')
    used={(r['row'],r['column']) for r in result['clusters'].values()}|exclusive_tiles(result)
    if share_ordinary:
        from ownpr_wide_lut_sharing import calibration
        result['mixed_wide_lut_calibration']=calibration()['admission_sha256']
        used=exclusive_tiles(result)|{(p['row'],p['column']) for n,p in result['clusters'].items() if p.get('ff') or p['slot'] in spec['leaves'].values() or design['modules']['top']['cells'][n]['type'] not in ('LUT1','LUT2','LUT3','LUT4')}
    options=[(site_cost(r,c) if site_cost else (r-old['row'])**2+(c-old['column'])**2,r,c) for r,row in enumerate(device.grid) for c,typ in enumerate(row) if typ==17 and (r,c) not in used]
    if not options:raise ValueError('No free calibrated CFU')
    score,r,c=min(options)
    if score==float('inf'):raise ValueError('No finite-cost calibrated wide site')
    for name,slot in spec['leaves'].items():
        paired=bool(ff and slot==spec['muxes'][root]['slot'])
        result['clusters'][name]=dict(lut=name,ff=ff if paired else None,row=r,column=c,slot=slot)
        if paired:result['clusters'][name]['wide_ff_root']=root
    result.setdefault('mux_clusters',{}).update({n:dict(row=r,column=c,**v) for n,v in spec['muxes'].items()})
    macro=dict(root=root,row=r,column=c,root_slot=spec['muxes'][root]['slot'])
    if share_ordinary:macro['ordinary_sharing']=True
    result.setdefault('wide_macros',[]).append(macro)
    result=validate_placement(design,result,device,dict(sites={}))
    return result,dict(old_site=[old['row'],old['column'],old['slot']],new_cfu=[r,c],fixed_other_cells=True,moved_ff=ff,
        new_ff_site=[r,c,spec['muxes'][root]['slot']] if ff else None,clock_replan_required=bool(ff),shared_ordinary_luts=share_ordinary,site_policy='Calibrated partial CFU' if share_ordinary else 'Caller-scored empty calibrated CFU' if site_cost else 'Nearest empty calibrated CFU to root; physical timing unproven')


def candidates(d,layout,ranking,include_paired=False):
    cells=d['modules']['top']['cells'];options=[];rejected=[];seen=set()
    constants={'0':'zero','1':'one'}
    for c in cells.values():
        if c['type'] in ('GND','VCC'):constants[next(iter(c['connections'].values()))[0]]='one' if c['type']=='VCC' else 'zero'
    for row in ranking['ranked_nets']:
        parent=row['driver']['instance'];slack,labels=critical_labels(row,ranking['traces'])
        if cells[parent]['type'] not in ('LUT1','LUT2','LUT3','LUT4'):continue
        for label in labels:
            root,pin=label.rsplit('/',1);key=(parent,root);site=layout['clusters'].get(root)
            if key in seen or not site or (site.get('ff') and not include_paired) or cells[root]['type'] not in ('LUT1','LUT2','LUT3','LUT4') or (site['row'],site['column']) in exclusive_tiles(layout):continue
            seen.add(key)
            try:
                late=late_boundary(d,parent,root,ranking['traces'],slack);_,change=transform(d,parent,root,late)
                if site.get('ff'):
                    from ownpr_wide_ff_packing import calibration
                    from ownpr_wide_ff_configuration import ROOT_SLOT
                    ff=cells[site['ff']]
                    if ff['type']!='DFFRE' or int(ff.get('parameters',{}).get('INIT','0'),2)!=0:raise ValueError('Uncalibrated paired FF type/initialization')
                    profile=dict(cell_type=ff['type'],ce=constants.get(ff['connections']['CE'][0],'signal'),control=constants.get(ff['connections']['RESET'][0],'signal'),clock=constants.get(ff['connections']['CLK'][0],'signal'))
                    evidence=calibration(profile['ce']=='signal')
                    if not any((q['tile_type'],q['level'],q['slot'],q['ff_profile'])==(17,change['cut_width'],ROOT_SLOT[change['cut_width']],profile) for q in evidence['records']):raise ValueError('Paired root lacks combined mode calibration')
            except ValueError as e:rejected.append(dict(parent=parent,root=root,reason=str(e)));continue
            wire=max(arc['wire_delay_ns'] for path in ranking['traces'] for arc in path['ordinary_wires'] if arc['net']==row['net'] and arc['endpoint']==label)
            options.append(dict(parent=parent,root=root,late_bit=late,worst_slack_ns=slack,reported_wire_ns=wire,cut_width=change['cut_width'],lut_growth=change['lut_growth'],old_receiver_pin=pin))
    options.sort(key=lambda r:(r['worst_slack_ns'],-r['reported_wire_ns'],r['lut_growth'],r['root']))
    return options,rejected


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('base','routes','sta','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--rank',type=int,default=0);p.add_argument('--include-paired',action='store_true',help='Allow a root-matched FF move only if its full combined profile is independently calibrated');a=p.parse_args()
    if a.rank<0:p.error('Negative rank')
    reserve(ROOT,40_000_000);start=time.monotonic();base,routes,sta,out=(v.resolve() for v in (a.base,a.routes,a.sta,a.out))
    verify_feedback(base,routes,sta);d=load_json(base/'packed.json');layout=load_json(base/'placement.json')
    sys.path.insert(0,str(ROOT/'experiments'));from analyze_ownpr_critical_wires import analyze
    ranking=analyze(sta,base,routes,32);options,rejected=candidates(d,layout,ranking,a.include_paired)
    if a.rank>=len(options):raise ValueError('No admissible wide timing cut')
    selected=options[a.rank];after,change=transform(d,selected['parent'],selected['root'],selected['late_bit']);placed,physical=place(after,layout,selected['root'],Device(),a.include_paired)
    out.mkdir(exist_ok=False);proof=prove(d,after,out/'cec',120)
    if not proof or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Wide timing cut CEC failed')
    for name,value in [('packed.json',after),('placement.json',placed),('candidates.json',dict(accepted=options,rejected=rejected))]:(out/name).write_text(json.dumps(value,indent=2))
    sources=[Path(__file__),ROOT/'scripts/ownpr_wide_cone.py',ROOT/'scripts/reorder_ownpr_wide_inputs.py',ROOT/'scripts/ownpr_wide_ff_packing.py',ROOT/'scripts/ownpr_wide_ff_configuration.py']
    for src in sources:shutil.copyfile(src,out/(src.name+'.txt'))
    result=dict(status='placed',netlist_sha256=sha(out/'packed.json'),placement_sha256=sha(out/'placement.json'),source_netlist_sha256=sha(base/'packed.json'),source_placement_sha256=sha(base/'placement.json'),source_routes_sha256=sha(routes/'routes.json'),source_sta_sha256=sha(sta/'probe.tr'),
        selected=selected,transform=change,physical=physical,proof=proof,source_sha256={src.name:sha(out/(src.name+'.txt')) for src in sources},
        synthesis_pnr_cooptimization=True,clock_endpoints_unchanged=not physical['clock_replan_required'],state_and_hard_resources_unchanged=True,interface_cycles_changed=False,physical_timing_verified=False,timing_signoff=False,vendor_pnr_executed=False,wall_seconds=time.monotonic()-start)
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(dict(selected=selected,physical=physical),indent=2))


if __name__=='__main__':main()
