"""Diagnostic fixed-layout OREG absorption, with a separately proved source rebuild.

Local feedback/enable equations are checked structurally. This does NOT prove
the complete source-to-incumbent-to-candidate correspondence: the transplant
remains ineligible for final frequency promotion until that gate is discharged.
"""
import argparse,copy,json,time
from pathlib import Path
from analyze_hard_register_absorption import analyze
from dsp_output_register_pack import validate,kept
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve


def prepare(design,placed,source_record,preserve_orphans=False):
    analysis=analyze(design);new=copy.deepcopy(design);layout=copy.deepcopy(placed)
    cells=new['modules']['top']['cells'];ground=next(c['connections']['G'][0] for c in cells.values() if c['type']=='GND')
    source={r['instance']:r for r in source_record['packed']};removed=[];history=[]
    for bank in analysis['banks']:
        name=bank['instance'];rows=bank['candidate_bits'];dsp=cells[name]
        if name not in source or bank['rejected_used_bits'] or len(bank['control_groups'])!=1:continue
        if kept(dsp.get('attributes',{})) or any(kept(cells[r['ff']].get('attributes',{})) or r['lut'] and kept(cells[r['lut']].get('attributes',{})) for r in rows):continue
        controls=bank['control_groups'][0]['controls'];enable=controls['effective_enable_and']
        if controls['reset']!='0' or not isinstance(enable,list) or len(enable)!=1:continue
        if {r['dsp_bit'] for r in rows}!={r['index'] for r in source[name]['bank']}:raise ValueError('Source and current output-bank coverage differs')
        if any(r['index']>=34 for r in source[name]['bank']):raise ValueError('Output bit outside native registered timing calibration')
        if any(cells[n]['type'] not in ('LUT1','LUT3') for n in [r['lut'] for r in rows if r['lut']]):raise ValueError('Unexpected feedback implementation')
        # CASO changes with OREG; SOA is excluded until separate cascade support.
        consumers={b for c in cells.values() for p,bs in c['connections'].items() if c['port_directions'][p]=='input' for b in bs}
        consumers.update(b for p in new['modules']['top']['ports'].values() if p['direction']=='output' for b in p['bits'])
        if any(b in consumers for p in ('CASO','SOA') for b in dsp['connections'][p]):raise ValueError('Observed DSP cascade output')
        if any(v!='BYPASS' for k,v in dsp['parameters'].items() if k.endswith('_CLK')):raise ValueError('Source DSP is already registered')
        for r in rows:
            ff=cells[r['ff']];lut=r['lut']
            clusters=[(n,g) for n,g in layout['clusters'].items() if g.get('ff')==r['ff']]
            if len(clusters)!=1 or clusters[0][0]!=lut or clusters[0][1]['lut']!=lut:raise ValueError('FF/LUT removal would disturb another cluster')
            dsp['connections']['DOUT'][r['dsp_bit']]=ff['connections']['Q'][0]
            del cells[r['ff']];del cells[lut];del layout['clusters'][lut];removed.extend([r['ff'],lut])
        dsp['parameters'].update(OREG_CLK='CLK0',OREG_CE='CE0',OREG_RESET='RESET0')
        dsp['connections'].update(CLK=[controls['clock'],ground],CE=[enable[0],ground],RESET=[ground,ground])
        dsp.setdefault('attributes',{})['ng_diagnostic_oreg']='1'
        history.append({'instance':name,'bits':len(rows),'controls':controls,'source_bank_indices':[r['index'] for r in source[name]['bank']]})
    if not history:raise ValueError('No supported OREG bank')
    relocated=[]
    if preserve_orphans:
        from ownpr_orphan_ff_calibration import calibration
        layout['orphan_ff_calibration']=calibration()
    ff_sites={(r['row'],r['column'],r['slot']) for r in layout['clusters'].values() if r.get('ff')}
    occupied={(r['row'],r['column'],r['slot']) for r in layout['clusters'].values()}
    keep_sites=set();keep_rejections=[]
    if preserve_orphans:
        from ownpr_orphan_ff_calibration import require_site
        from ownpr_shared_ff_packing import constants
        from ownpr_device import Device
        device=Device();constant=constants(new)
        excluded={(r['row'],r['column']) for key in ('arithmetic_clusters','mux_clusters') for r in layout.get(key,{}).values()}
        requested={(r['row'],r['column']) for r in layout['clusters'].values() if r.get('ff') and r['slot']%2 and not r.get('wide_ff_root') and (r['row'],r['column'],r['slot']-1) not in ff_sites}
        for site in sorted(requested):
            members=[r for r in layout['clusters'].values() if r.get('ff') and (r['row'],r['column'])==site]
            try:require_site(cells,members,layout,device,site,excluded,constant);keep_sites.add(site)
            except ValueError as error:keep_rejections.append({'site':list(site),'reason':str(error)})
    for name,row in layout['clusters'].items():
        if not row.get('ff') or row.get('wide_ff_root') or row['slot']%2==0:continue
        base=row['slot']-1;target=(row['row'],row['column'],base)
        if target in ff_sites:continue
        if (row['row'],row['column']) in keep_sites:continue
        if base not in (0,2,4) or target in occupied:raise ValueError('Absorption leaves an odd-slot FF without a free calibrated even slot')
        old=(row['row'],row['column'],row['slot']);row['slot']=base
        occupied.remove(old);occupied.add(target);ff_sites.remove(old);ff_sites.add(target)
        if row['ff'] in layout.get('fixed_ff_sites',{}):layout['fixed_ff_sites'][row['ff']]=list(target)
        relocated.append({'lut':name,'ff':row['ff'],'before':list(old),'after':list(target)})
    layout.update(physical_legality_complete=False,timing_signoff=False,dsp_register_absorption=history)
    layout.pop('weighted_internal_manhattan',None)
    return new,layout,{'banks':history,'removed_cells':removed,'orphan_ff_relocations':relocated,
                      'orphan_preserved_sites':[list(s) for s in sorted(keep_sites)],'orphan_preserve_rejections':keep_rejections,'analysis':analysis}


def main():
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--source-resources',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--preserve-orphan-slots',action='store_true');a=p.parse_args()
    reserve(Path(__file__).resolve().parents[1],40_000_000);start=time.time()
    source=load_json(a.source_resources/'resources.json');record=load_json(a.source_resources/'dsp_output_packing.json')
    validate(source,record,a.source_resources)
    if sha(a.source_resources/'coarse_source.json')!=record['input_sha256']:raise ValueError('Source coarse state certificate changed')
    design,placed,change=prepare(load_json(a.base/'packed.json'),load_json(a.base/'placement.json'),record,a.preserve_orphan_slots)
    from ownpr_device import Device
    from ownpr_hard_architecture import HardArchitecture
    from ownpr_ff_configuration import compile_initial
    from ownpr_mux_macros import validate as wide_validate
    from ownpr_carry_macros import validate as carry_validate
    device=Device();hard=HardArchitecture().validate(design,placed,device)
    compile_initial(design,placed);wide_validate(design,placed,device);carry_validate(design,placed,device)
    a.out.mkdir(parents=True,exist_ok=False)
    (a.out/'packed.json').write_text(json.dumps(design));(a.out/'placement.json').write_text(json.dumps(placed,indent=2))
    (a.out/'change.json').write_text(json.dumps(change,indent=2)+'\n')
    report={'status':'placed','diagnostic_only':True,'whole_source_equivalence_proved':False,'frequency_promotion_allowed':False,
            'source_rebuild_local_transition_proved':True,'source_packing_sha256':sha(a.source_resources/'dsp_output_packing.json'),
            'source_netlist_sha256':sha(a.base/'packed.json'),'source_placement_sha256':sha(a.base/'placement.json'),
            'netlist_sha256':sha(a.out/'packed.json'),'placement_sha256':sha(a.out/'placement.json'),
            'hard_validation':hard,'clock_endpoints_unchanged':False,'state_and_hard_resources_unchanged':False,
            'scope':__doc__,'wall_seconds':time.time()-start,'implementation_sha256':sha(Path(__file__))}
    (a.out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'banks':len(change['banks']),'removed_cells':len(change['removed_cells']),'hard':hard,'diagnostic_only':True}))


if __name__=='__main__':main()
