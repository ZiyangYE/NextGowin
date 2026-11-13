"""Move an unpaired LUT into calibrated spare slots beside a wide-root FF."""
import argparse,copy,json,shutil
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from ownpr_wide_lut_sharing import calibration
from ownpr_wide_ff_lut_sharing import ADMISSION_SHA256
from ownpr_mux_macros import validate
from ownpr_carry_macros import validate as validate_carry
from ownpr_ff_configuration import compile_initial
from ownpr_mux_configuration import compile_wide
from workspace_budget import reserve


def transform(design,placed,lut,root,slot):
    cells=design['modules']['top']['cells'];old=placed['clusters'].get(lut)
    if old is None or old.get('ff') or old.get('wide_ff_root') or cells[lut]['type'] not in ('LUT1','LUT2','LUT3','LUT4'):raise ValueError('Only an unpaired ordinary LUT can move')
    result=copy.deepcopy(placed);matches=[m for m in result.get('wide_macros',[]) if m['root']==root]
    if len(matches)!=1:raise ValueError('Missing unique target macro')
    macro=matches[0];site=(macro['row'],macro['column'],slot)
    if any((r['row'],r['column'],r['slot'])==site for r in placed['clusters'].values()):raise ValueError('Target slot is occupied')
    if not any(r.get('wide_ff_root')==root for r in placed['clusters'].values()):raise ValueError('Target has no wide-root FF')
    result['clusters'][lut].update(row=site[0],column=site[1],slot=site[2])
    macro.update(ordinary_sharing=True,wide_ff_ordinary_sharing=True)
    result['mixed_wide_lut_calibration']=calibration()['admission_sha256'];result['mixed_wide_ff_lut_calibration']=ADMISSION_SHA256
    device=Device();check=validate(design,result,device);validate_carry(design,result,device)
    ff=compile_initial(design,result);wide=compile_wide(design,result,device)
    a=next(x for x in ff['sites'] if (x['row'],x['column'])==site[:2]);b=next(x for x in wide['sites'] if (x['row'],x['column'])==site[:2])
    if a['groups']!=b['groups']:raise ValueError('FF and wide-LUT configurations disagree')
    if result.get('hard_clusters'):
        from ownpr_hard_architecture import HardArchitecture
        HardArchitecture().validate(design,result,device)
    result.update(physical_legality_complete=False,timing_signoff=False)
    record=dict(lut=lut,root=root,old_site=[old[k] for k in ('row','column','slot')],new_site=list(site),netlist_unchanged=True,fixed_io_unchanged=result['fixed_io']==placed['fixed_io'],clock_endpoints_unchanged=True,mixed_calibration_sha256=ADMISSION_SHA256,full_route_sta_required=True)
    return result,record,check


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--lut',required=True);p.add_argument('--root',required=True);p.add_argument('--slot',type=int,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,10000000);m=load_json(a.base/'manifest.json')
    if m['status']!='placed' or m['netlist_sha256']!=sha(a.base/'packed.json') or m['placement_sha256']!=sha(a.base/'placement.json'):raise ValueError('Source placement changed')
    changed,record,audit=transform(load_json(a.base/'packed.json'),load_json(a.base/'placement.json'),a.lut,a.root,a.slot)
    a.out.mkdir(exist_ok=False);shutil.copyfile(a.base/'packed.json',a.out/'packed.json');(a.out/'placement.json').write_text(json.dumps(changed,indent=2))
    m.update(netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),source_placement_sha256=sha(a.base/'placement.json'),mixed_wide_ff_move=record,timing_signoff=False)
    m['mixed_packing_source_sha256']={n:sha(ROOT/'scripts'/n) for n in ('prepare_wide_ff_lut_neighbor.py','ownpr_wide_ff_lut_sharing.py','ownpr_wide_ff_packing.py','ownpr_wide_lut_sharing.py')}
    (a.out/'manifest.json').write_text(json.dumps(m,indent=2));(a.out/'packing_audit.json').write_text(json.dumps(audit,indent=2));print(json.dumps(record,indent=2))
