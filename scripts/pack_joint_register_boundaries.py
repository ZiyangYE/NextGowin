"""Generic calibrated wide-root/FF absorption and shared-control packing.

Only exact identity adapters and native-proved profiles are admitted. Pins and
interface cycles stay fixed; moved FF clocks must be rebuilt and native STA
must check every timing class. Geometry is a proposal prior, never a QoR claim.
"""
import argparse,json,time
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from ownpr_pack_wide_ff import transform
from ownpr_pack_shared_ff import propose
from ownpr_ff_configuration import compile_initial
from ownpr_mux_macros import validate as validate_mux
from native_boundary_cec import prove
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('base','out','budget'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,50000000);budget=load_json(a.budget)
    if time.time()+600>budget['deadline_epoch']:raise TimeoutError('Insufficient original complete-attempt budget')
    old=load_json(a.base/'packed.json');layout=load_json(a.base/'placement.json');design,placed=old,layout;changes=[];rejected=[];start=time.monotonic()
    for name,row in sorted(layout['clusters'].items()):
        if not row.get('ff') or row.get('wide_ff_root') or name not in placed['clusters']:continue
        try:nd,np,change=transform(design,placed,name)
        except ValueError as error:rejected.append(dict(cell=name,reason=str(error)));continue
        design,placed=nd,np;changes.append(change)
    shared=None
    try:placed,shared=propose(design,placed,Device(),{},count=64,radius=24)
    except ValueError as error:rejected.append(dict(operator='shared_control_pair',reason=str(error)))
    compile_initial(design,placed)
    if placed.get('wide_macros'):validate_mux(design,placed,Device())
    if layout['fixed_io']!=placed['fixed_io'] or layout.get('hard_clusters')!=placed.get('hard_clusters'):raise ValueError('Fixed IO/hard sites changed')
    a.out.mkdir(exist_ok=False);proof=prove(old,design,a.out/'cec',100)
    if old!=design and (not proof or not all(r['cec']['equivalent'] for r in proof)):raise ValueError('Joint FF-boundary CEC failed')
    (a.out/'packed.json').write_text(json.dumps(design,indent=2));(a.out/'placement.json').write_text(json.dumps(placed,indent=2))
    meta=load_json(a.base/'manifest.json');meta.update(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),source_netlist_sha256=sha(a.base/'packed.json'),source_placement_sha256=sha(a.base/'placement.json'),
        joint_register_boundary_changes=changes,shared_control_pair_plan=shared,profile_rejections=rejected,proof=proof,clock_replan_required=True,clock_endpoints_unchanged=False,
        source_constraints_preserved=True,interface_cycles_changed=False,native_timing_after_state_moves_predicted=False,timing_signoff=False,
        wall_seconds=time.monotonic()-start,original_budget=budget,source_sha256={str(x.resolve()):sha(x) for x in (Path(__file__),ROOT/'scripts/ownpr_pack_wide_ff.py',ROOT/'scripts/ownpr_pack_shared_ff.py',ROOT/'scripts/ownpr_ff_configuration.py')})
    (a.out/'manifest.json').write_text(json.dumps(meta,indent=2));print(json.dumps(dict(absorbed_identity_luts=len(changes),shared_pairs=shared['shared_pairs'] if shared else 0,clock_replan_required=True)))


if __name__=='__main__':main()
