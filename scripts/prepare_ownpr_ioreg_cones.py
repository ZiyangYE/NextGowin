"""Move combinational fanin cones of already packed, fixed-pad output registers."""
import argparse,json,shutil
from pathlib import Path
from ownpr_device import ROOT
from ownpr_place import sha
from ownpr_ioreg_cone import pull_cones
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--depth',type=int,default=3)
    p.add_argument('--fraction',type=float,default=.6)
    a=p.parse_args();base=a.base.resolve();out=a.out.resolve()
    reserve(ROOT,10_000_000)
    meta=json.loads((base/'manifest.json').read_text())
    if meta['status']!='placed' or sha(base/'placement.json')!=meta['placement_sha256'] or sha(base/'packed.json')!=meta['netlist_sha256']:
        raise ValueError('Source placement changed')
    design=json.loads((base/'packed.json').read_text());placed=json.loads((base/'placement.json').read_text())
    changed,report=pull_cones(design,placed,a.depth,a.fraction)
    out.mkdir(exist_ok=False);shutil.copyfile(base/'packed.json',out/'packed.json')
    (out/'placement.json').write_text(json.dumps(changed,indent=2))
    (out/'cone_moves.json').write_text(json.dumps(report,indent=2))
    meta.update(status='placed',source_placement=str(base),source_placement_sha256=sha(base/'placement.json'),
        placement_sha256=sha(out/'placement.json'),netlist_sha256=sha(out/'packed.json'),
        ioreg_cone_relocation=report,logical_netlist_byte_identical=True,state_cells_changed=False,
        pipeline_stages_added=0,timing_signoff=False,physical_legality_complete=False)
    (out/'manifest.json').write_text(json.dumps(meta,indent=2))
    print(json.dumps(dict(moved_luts=len(report['moves']),depth=a.depth,fraction=a.fraction)))


if __name__=='__main__':main()
