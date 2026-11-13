"""Prepare a boundary-CEC-checked local LUT replica on a modern checkpoint.

Preserves existing physical bindings, including I/O FFs and hard blocks.
The source may itself be experimental; CEC proves only this transformation.
"""
import argparse,json,time,shutil,copy
from pathlib import Path
from ownpr_device import Device,ROOT
from ownpr_image_storage import load_json,sha
from ownpr_lut_replication import replicate
from ownpr_critical_chain_move import exclusive_tiles
from workspace_budget import reserve


def reuse_existing(design,layout,source,replica,sinks):
    cells=design['modules']['top']['cells'];ordinary=('LUT1','LUT2','LUT3','LUT4')
    if source==replica or any(n not in layout['clusters'] or cells[n]['type'] not in ordinary for n in (source,replica)):raise ValueError('Existing replica must be an ordinary LUT')
    if not sinks or len(set(map(tuple,sinks)))!=len(sinks):raise ValueError('Missing/duplicate replica sink')
    old=cells[source]['connections']['F'];new=cells[replica]['connections']['F'];after=copy.deepcopy(design)
    for n,p in sinks:
        if n in (source,replica) or n not in layout['clusters'] or cells[n]['type'] not in ordinary or cells[n]['port_directions'].get(p)!='input' or cells[n]['connections'].get(p)!=old:raise ValueError('Unsupported existing-replica data edge')
        after['modules']['top']['cells'][n]['connections'][p]=list(new)
    return after,copy.deepcopy(layout),dict(source=source,replica=replica,old_bit=old[0],new_bit=new[0],sinks=sinks,lut_growth=0,existing_replica_reused=True,equivalence_pending=True,
        clocks_changed=False,state_storage_changed=False,fixed_io_changed=False,ranking='Explicit candidate; full boundary CEC proves function equivalence, full STA decides physical benefit')


def prepare(base,source,sinks,cst,sdc,out,existing_replica=None):
    reserve(ROOT,100_000_000);started=time.monotonic()
    design=load_json(base/'packed.json');layout=load_json(base/'placement.json');meta=load_json(base/'manifest.json')
    if meta['status']!='placed' or meta['netlist_sha256']!=sha(base/'packed.json') or meta['placement_sha256']!=sha(base/'placement.json'):
        raise ValueError('Source placement hash mismatch')
    from ownpr_place import constraints
    contract=constraints(design['modules']['top'],cst.read_text(),sdc.read_text())
    device=Device()
    new,placed,change=reuse_existing(design,layout,source,existing_replica,sinks) if existing_replica else replicate(design,layout,device,source,sinks)
    replica=change['replica'];site=placed['clusters'][replica]
    if (site['row'],site['column']) in exclusive_tiles(layout):raise ValueError('Replica conflicts with an exclusive resource tile')
    for key,value in layout.items():
        if key.endswith('_clusters') or key=='fixed_io':
            if placed.get(key)!=value:raise ValueError('Existing macro/IO binding changed')
    if any(placed['clusters'][n]!=row for n,row in layout['clusters'].items()):raise ValueError('Original cluster moved')
    if new['modules']['top']['ports']!=design['modules']['top']['ports']:raise ValueError('Interface changed')
    from ownpr_ioreg_macros import validate as validate_io
    from ownpr_mux_macros import validate as validate_mux
    from ownpr_carry_macros import validate as validate_carry
    from ownpr_ff_configuration import compile_initial
    validate_io(new,placed,device);validate_mux(new,placed,device);validate_carry(new,placed,device);compile_initial(new,placed)
    if placed.get('hard_clusters'):
        from ownpr_hard_architecture import HardArchitecture
        HardArchitecture().validate(new,placed,device)
    for n,pad in placed['fixed_io'].items():
        if contract['pins'][pad['port']]!=pad['package_pin'] or contract['properties'].get(pad['port'],'')!=pad['electrical']:
            raise ValueError('Fixed pin/electrical contract changed')
    out.mkdir(exist_ok=False)
    from native_boundary_cec import prove
    proof=prove(design,new,out/'cec',120)
    if not proof or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Incomplete boundary equivalence proof')
    if existing_replica:change['equivalence_pending']=False
    for name,value in [('packed.json',new),('placement.json',placed),('replication.json',change)]:
        (out/name).write_text(json.dumps(value,indent=2))
    sources={}
    for name in ('prepare_logic_replica.py','ownpr_lut_replication.py'):
        f=ROOT/'scripts'/name;shutil.copyfile(f,out/(name+'.txt'));sources[name]=sha(f)
    result=dict(status='placed',netlist_sha256=sha(out/'packed.json'),placement_sha256=sha(out/'placement.json'),
        source_netlist_sha256=sha(base/'packed.json'),source_placement_sha256=sha(base/'placement.json'),
        source_manifest_sha256=sha(base/'manifest.json'),cst_sha256=sha(cst),sdc_sha256=sha(sdc),replication=change,
        proof=proof,proof_scope='Boundary CEC relative to supplied checkpoint; does not independently prove earlier sequential transformations',
        source_helpers_sha256=sources,existing_physical_bindings_unchanged=True,clock_replan_required=False,
        timing_signoff=False,physical_legality_complete=False,wall_seconds=time.monotonic()-started)
    (out/'manifest.json').write_text(json.dumps(result,indent=2));return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('base','cst','sdc','out'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--source',required=True);p.add_argument('--sink',action='append',required=True,help='cell/pin; repeat for a selected sink group')
    p.add_argument('--existing-replica',help='Reuse an existing ordinary LUT instead of adding a cell; equivalence still requires CEC')
    a=p.parse_args();print(json.dumps(prepare(a.base,a.source,[s.rsplit('/',1) for s in a.sink],a.cst,a.sdc,a.out,a.existing_replica),indent=2))
