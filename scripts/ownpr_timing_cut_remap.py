"""Use routed STA to absorb a critical LUT edge into one ordinary LUT cut.

The parent is retained for its other loads; the root's output and physical
site are preserved. Full boundary CEC, incremental own P&R and complete STA
are mandatory. Eliminating a measured wire is not a prediction of net gain:
new root input routes and competing paths still have to be measured.
"""
import argparse,copy,json,shutil,sys,time
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from register_control_map import logic,normalize
from ownpr_critical_chain_move import exclusive_tiles,apply as validate_placement
from ownpr_critical_tree_rebuild import verify_feedback,critical_labels
from native_boundary_cec import prove
from workspace_budget import reserve

LUTS={'LUT1','LUT2','LUT3','LUT4'}


def compose(design,parent,root):
    cells=design['modules']['top']['cells']
    if parent==root or cells[parent]['type'] not in LUTS or cells[root]['type'] not in LUTS:raise ValueError('Cut requires two distinct ordinary LUTs')
    constants={'0':'0','1':'1'}
    for c in cells.values():
        if c['type'] in ('GND','VCC'):constants[next(iter(c['connections'].values()))[0]]='1' if c['type']=='VCC' else '0'
    def function(cell):
        ins,out,truth=logic(cell)
        if set(cell['parameters'])!={'INIT'} or len(cell['parameters']['INIT'])!=1<<len(ins) or set(cell['parameters']['INIT'])-{'0','1'}:raise ValueError('Unverified ordinary LUT parameters')
        support,word=normalize([constants.get(b,b) for b in ins],truth)
        return list(support),out,word
    ai,intermediate,at=function(cells[parent]);bi,output,bt=function(cells[root])
    if intermediate not in bi or output in ai or intermediate in ai:raise ValueError('No acyclic dependent LUT edge')
    boundary=list(dict.fromkeys([b for b in bi if b!=intermediate]+ai))
    if not 1<=len(boundary)<=4:raise ValueError('Cut does not fit an ordinary LUT')
    word=0
    for assignment in range(1<<len(boundary)):
        values={b:assignment>>i&1 for i,b in enumerate(boundary)}
        values[intermediate]=at>>sum(values[b]<<i for i,b in enumerate(ai))&1
        word|=(bt>>sum(values[b]<<i for i,b in enumerate(bi))&1)<<assignment
    support,truth=normalize(boundary,word);support=list(support)
    if not support:raise ValueError('Constant cuts require a separate constant-resource policy')
    after=copy.deepcopy(design);cell=after['modules']['top']['cells'][root];k=len(support)
    cell.update(type='LUT'+str(k),parameters={'INIT':format(truth,f'0{1<<k}b')},
        port_directions={**{f'I{i}':'input' for i in range(k)},'F':'output'},
        connections={**{f'I{i}':[b] for i,b in enumerate(support)},'F':[output]})
    return after,dict(parent_preserved=parent,replaced_root=root,intermediate_bit=intermediate,output_bit=output,
        boundary_bits=support,truth_hex=hex(truth),before_type=cells[root]['type'],after_type=cell['type'],
        lut_growth=0,interface_cycles_changed=False,physical_gain_unproven=True)


def candidates(design,placed,ranking):
    cells=design['modules']['top']['cells'];excluded=exclusive_tiles(placed);options=[];rejected=[];seen=set()
    for row in ranking['ranked_nets']:
        parent=row['driver']['instance']
        if cells[parent]['type'] not in LUTS:continue
        slack,labels=critical_labels(row,ranking['traces'])
        for label in labels:
            root,pin=label.rsplit('/',1);key=parent,root
            if key in seen:continue
            seen.add(key);site=placed['clusters'].get(root)
            if cells[root]['type'] not in LUTS or not site or site.get('lut')!=root or (site['row'],site['column']) in excluded:continue
            try:_,change=compose(design,parent,root)
            except ValueError as e:rejected.append(dict(parent=parent,root=root,reason=str(e)));continue
            wire=max(a['wire_delay_ns'] for p in ranking['traces'] for a in p['ordinary_wires'] if a['net']==row['net'] and a['endpoint']==label)
            options.append(dict(**change,reported_wire_ns=wire,worst_path_slack_ns=slack,parent_fanout=row['fanout'],old_receiver_pin=pin,
                ranking_scope='Worst routed setup slack then eliminated wire length; added input loads and routes require full P&R.'))
    return sorted(options,key=lambda r:(r['worst_path_slack_ns'],-r['reported_wire_ns'],len(r['boundary_bits']),r['replaced_root'])),rejected


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('base','routes','sta','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--rank',type=int,default=0);a=p.parse_args()
    if a.rank<0:p.error('Negative candidate rank')
    reserve(ROOT,30_000_000);start=time.monotonic();base,routes,sta,out=(v.resolve() for v in (a.base,a.routes,a.sta,a.out))
    verify_feedback(base,routes,sta);design=load_json(base/'packed.json');placed=load_json(base/'placement.json')
    sys.path.insert(0,str(ROOT/'experiments'));from analyze_ownpr_critical_wires import analyze
    ranking=analyze(sta,base,routes,32);options,rejected=candidates(design,placed,ranking)
    if a.rank>=len(options):raise ValueError('No admissible timing-driven cut at this rank')
    selected=options[a.rank];after,change=compose(design,selected['parent_preserved'],selected['replaced_root'])
    validate_placement(after,placed,Device(),dict(sites={}))
    out.mkdir(exist_ok=False);shutil.copyfile(__file__,out/'producer_source.py.txt')
    proof=prove(design,after,out/'cec',120)
    if not proof or not all(r['cec']['equivalent'] for r in proof):raise ValueError('Timing cut CEC failed')
    (out/'packed.json').write_text(json.dumps(after,indent=2));shutil.copyfile(base/'placement.json',out/'placement.json')
    (out/'candidates.json').write_text(json.dumps(dict(accepted=options,rejected=rejected),indent=2))
    result=dict(status='placed',netlist_sha256=sha(out/'packed.json'),placement_sha256=sha(out/'placement.json'),
        source_netlist_sha256=sha(base/'packed.json'),source_placement_sha256=sha(base/'placement.json'),source_routes_sha256=sha(routes/'routes.json'),source_sta_sha256=sha(sta/'probe.tr'),
        selected=selected,proof=proof,candidates=len(options),rejected_candidates=len(rejected),producer_sha256=sha(out/'producer_source.py.txt'),
        placement_byte_identical=True,clock_endpoints_unchanged=True,state_and_hard_resources_unchanged=True,logical_cycles_preserved=True,
        synthesis_pnr_cooptimization=True,vendor_pnr_executed=False,timing_signoff=False,physical_timing_verified=False,wall_seconds=time.monotonic()-start)
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(selected,indent=2))


if __name__=='__main__':main()
