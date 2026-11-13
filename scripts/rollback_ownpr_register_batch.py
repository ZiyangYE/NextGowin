"""Roll back batch members whose FF endpoints violate setup in audited STA.

This is a bounded repair of an infeasible candidate, not acceptance of its
remaining changes. A fresh complete P&R export is still mandatory.
"""
import argparse,copy,json,shutil,time
from pathlib import Path
from ownpr_image_storage import load_json,read_text,sha
from ownpr_route_eco import violations
from ownpr_device import Device,ROOT
from ownpr_ff_configuration import compile_initial
from ownpr_mux_macros import validate
from ownpr_carry_macros import validate as validate_carry
from ownpr_hard_architecture import HardArchitecture


def rollback(design,reference,placed,moves,offenders):
    selected=[m for m in moves if {m['ff'],m.get('swap_partner',{}).get('ff') if m.get('swap_partner') else None}&set(offenders)]
    if not selected:raise ValueError('No moved FF endpoint matches the setup violations')
    updated=copy.deepcopy(placed);restored=[]
    for move in selected:
        names=[move['lut']]+([move['swap_partner']['lut']] if move.get('swap_partner') else [])
        for name in names:
            if name in restored:raise ValueError('Overlapping batch rollback')
            before=reference['clusters'][name];current=placed['clusters'][name]
            if before['ff']!=current['ff'] or before['lut']!=current['lut']:raise ValueError('Rollback changed LUT/FF identity')
            updated['clusters'][name]=copy.deepcopy(before);restored.append(name)
    device=Device();sites=[(r['row'],r['column'],r['slot']) for r in updated['clusters'].values()]
    if len(sites)!=len(set(sites)):raise ValueError('Register rollback overlaps another cluster')
    validate(design,updated,device);validate_carry(design,updated,device);compile_initial(design,updated)
    if updated.get('hard_clusters'):HardArchitecture().validate(design,updated,device)
    remaining=[m for m in moves if m not in selected]
    updated['register_batch_rollback']=dict(offending_ffs=sorted(offenders),restored_clusters=restored)
    return updated,selected,remaining


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('reference','base','sta','out'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();from workspace_budget import reserve
    reserve(ROOT,30_000_000);start=time.monotonic();meta=load_json(a.base/'manifest.json');sta=load_json(a.sta/'manifest.json');audit=load_json(a.sta/'handoff_audit.json')
    if meta['source_placement_sha256']!=sha(a.reference/'placement.json') or sha(a.reference/'packed.json')!=sha(a.base/'packed.json'):raise ValueError('Register batch reference changed')
    if sta['packed_sha256']!=sha(a.base/'packed.json') or sta['placement_sha256']!=sha(a.base/'placement.json') or sta['handoff_audit_sha256']!=sha(a.sta/'handoff_audit.json') or audit['source_sha256']['probe.tr']!=sha(a.sta/'probe.tr') or audit['status']!='initial_handoff_checks_passed':raise ValueError('Rollback requires matching audited STA')
    reverse={v:k for k,v in load_json(a.sta/'name_map.json')['instances'].items()};offenders={reverse[endpoint.rpartition('/')[0]] for _,_,endpoint in violations(read_text(a.sta/'probe.tr'),'Setup')}
    design=load_json(a.base/'packed.json');placed=load_json(a.base/'placement.json');reference=load_json(a.reference/'placement.json')
    updated,removed,remaining=rollback(design,reference,placed,meta['register_moves'],offenders)
    def clocks(p):return {(r['row'],r['column'],r['slot']//2) for r in p['clusters'].values() if r.get('ff')}
    unchanged=clocks(placed)==clocks(updated);a.out.mkdir(exist_ok=False);shutil.copyfile(a.base/'packed.json',a.out/'packed.json');shutil.copyfile(__file__,a.out/'operator_source.py.txt')
    (a.out/'placement.json').write_text(json.dumps(updated,indent=2));meta.update(status='placed',placement_sha256=sha(a.out/'placement.json'),netlist_sha256=sha(a.out/'packed.json'),
        source_placement_sha256=sha(a.base/'placement.json'),source_sta_sha256=sha(a.sta/'probe.tr'),register_moves=remaining,rolled_back_moves=removed,
        logical_netlist_byte_identical=True,clock_endpoints_unchanged=unchanged,operator_sha256=sha(a.out/'operator_source.py.txt'),wall_seconds=time.monotonic()-start,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(dict(removed=removed,remaining=len(remaining),clock_endpoints_unchanged=unchanged),indent=2))


if __name__=='__main__':main()
