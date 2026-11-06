"""Compare verified own-P&R candidates under one frozen timing/I/O contract.

Selection is an optimization checkpoint, never a claim of timing closure or
complete hardware validity. The selected netlist gets a fresh direct CEC.
"""
from ownpr_image_storage import read_text as read_artifact_text
import argparse,json,re
from pathlib import Path
from collections import Counter
from ownpr_place import sha
from run_ownpr_pin_closure import metric,improves
from audit_ownpr_native_export import audit as export_audit
from audit_ownpr_joint_routes import audit as joint_audit
from native_boundary_cec import prove
from extract_timing import ROW


def read(path):return json.loads(Path(path).read_text())


def resource_vector(counts):
    result=Counter()
    for kind,count in counts.items():
        key='LUT' if re.fullmatch(r'LUT[1-4]',kind) else 'FF' if kind.startswith('DFF') else 'MUX' if kind.startswith('MUX2_LUT') else kind
        result[key]+=count
    return dict(result)


def area_tiebreak(before,after,old_counts,new_counts):
    """Prefer a strict resource reduction only with identical timing metrics."""
    keys=('setup_wns_ns','hold_wns_ns','setup_violations','hold_violations','total_negative_setup_slack_ns','reported_fmax_mhz','other_checks_passed','native_constraints_satisfied')
    if any(before[k]!=after[k] for k in keys) or after['hold_violations'] or after['hold_wns_ns']<0 or not after['other_checks_passed']:return False
    a,b=resource_vector(old_counts),resource_vector(new_counts);types=set(a)|set(b)
    return all(b.get(k,0)<=a.get(k,0) for k in types) and any(b.get(k,0)<a.get(k,0) for k in types)


def check_contract(base,candidate,base_tree,candidate_tree,allow_clock_change=False):
    for field in ('cst_sha256','source_sdc_sha256'):
        if candidate[field]!=base[field]:raise ValueError('Candidate changed fixed constraint: '+field)
    if candidate_tree['source']!=base_tree['source']:raise ValueError('Candidate changed the fixed clock source')
    if candidate['clock_tree_sha256']!=base['clock_tree_sha256'] and not allow_clock_change:raise ValueError('Candidate changed frozen clock tree')


def worst_path(folder,placement):
    folder=Path(folder);placed=read(Path(placement)/'placement.json');mapping=read(folder/'name_map.json');reverse={v:k for k,v in mapping['instances'].items()}
    report=read_artifact_text(folder/'probe.tr');match=re.search(r'^3\.3\.1 Setup Analysis Report\n.*?Data Arrival Path:\n(.*?)(?=\nData Required Path:)',report,re.S|re.M)
    if not match:raise ValueError('Missing worst setup arrival path')
    rows=[]
    for line in match[1].splitlines():
        row=ROW.match(line)
        if not row:continue
        arrival,delay,kind,transition,fanout,location,endpoint=row.groups();alias,_,pin=endpoint.rpartition('/')
        if alias not in reverse:continue
        name=reverse[alias];fixed=placed['fixed_io'].get(name,{})
        rows.append(dict(instance=name,pin=pin,kind=kind,transition=transition,arrival_ns=float(arrival),delay_ns=float(delay),
            physical_location=location,port=fixed.get('port'),package_pin=fixed.get('package_pin')))
    return dict(rows=rows,scope='One worst setup arrival path; rounded native report values, not an exhaustive timing graph.')


def main():
    p=argparse.ArgumentParser();p.add_argument('--base-placement',type=Path,required=True);p.add_argument('--base-sta',type=Path,required=True)
    p.add_argument('--candidate',nargs=4,action='append',required=True,metavar=('PLACEMENT','ROUTES','WIRE','EXPORT'))
    p.add_argument('--allow-clock-tree-change',action='store_true',help='Allow independently audited internal clock trees while retaining source, CST and SDC')
    p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(exist_ok=False)
    base=read(a.base_sta/'manifest.json');best=metric(a.base_sta);original=dict(best);history=[]
    best_counts=dict(Counter(c['type'] for c in read(a.base_placement/'packed.json')['modules']['top']['cells'].values()))
    if sha(a.base_placement/'packed.json')!=base['packed_sha256'] or sha(a.base_placement/'placement.json')!=base['placement_sha256']:raise ValueError('Baseline placement does not match final timing')
    selected=dict(placement=str(a.base_placement),export=str(a.base_sta));sources={}
    if sha(a.base_sta/'clock_tree.json')!=base['clock_tree_sha256']:raise ValueError('Baseline clock witness changed')
    base_tree=read(a.base_sta/'clock_tree.json')
    for paths in a.candidate:
        placement,routes,wire,sta=map(Path,paths);meta=read(sta/'manifest.json')
        if sha(sta/'clock_tree.json')!=meta['clock_tree_sha256'] or sha(routes/'clock_tree.json')!=meta['clock_tree_sha256']:raise ValueError('Candidate clock witnesses differ')
        check_contract(base,meta,base_tree,read(sta/'clock_tree.json'),a.allow_clock_tree_change)
        if meta['routes_sha256']!=sha(routes/'routes.json'):raise ValueError('Candidate routing does not match final timing')
        joint=joint_audit(placement,routes,wire);checked=export_audit(sta,placement);observed=metric(sta)
        if sha(placement/'placement.json')!=meta['placement_sha256']:raise ValueError('Candidate placement changed')
        state=dict(placement=str(placement),routes=str(routes),wire=str(wire),export=str(sta))
        counts=dict(Counter(c['type'] for c in read(placement/'packed.json')['modules']['top']['cells'].values()))
        smaller=area_tiebreak(best,observed,best_counts,counts);accept=improves(best,observed) or smaller
        history.append(dict(**state,decision='accepted' if accept else 'rejected_global_timing',metrics=observed,
            resource_reduction_at_identical_timing=smaller,resource_vector=resource_vector(counts),
            primitive_counts=counts,configuration_bits_verified=checked['configuration_bits_verified'],
            mux_timing=checked['timing'].get('mux_queries'),ioreg_timing=checked['timing'].get('ioreg'),joint_occupancy=joint,
            clock_tree_changed=meta['clock_tree_sha256']!=base['clock_tree_sha256']))
        for file in (placement/'packed.json',placement/'placement.json',routes/'routes.json',wire/'wire_delays.json',sta/'manifest.json',sta/'handoff_audit.json',sta/'probe.tr',sta/'probe.fs'):
            sources[str(file)]=sha(file)
        if accept:best=observed;selected=state;best_counts=counts
    proof=prove(read(a.base_placement/'packed.json'),read(Path(selected['placement'])/'packed.json'),a.out/'cec',120)
    final=dict(status='verified_candidate_comparison_complete',baseline=dict(placement=str(a.base_placement),export=str(a.base_sta),metrics=original),
        candidates=history,selected=selected,metrics=best,direct_selected_cec=proof,
        worst_setup_path=worst_path(selected['export'],selected['placement']),source_sha256=sources,
        fixed_io_and_source_sdc_preserved=True,clock_source_preserved=True,internal_clock_changes_permitted=a.allow_clock_tree_change,
        vendor_placement_executed=False,vendor_routing_executed=False,open_chip_database_used=False,
        timing_signoff=False,full_bitstream_verified=False,scope='Supported-slice candidate comparison; 200 MHz and general/hardware acceptance remain independent gates.')
    (a.out/'summary.json').write_text(json.dumps(final,indent=2));print(json.dumps(dict(selected=selected,metrics=best),indent=2))


if __name__=='__main__':main()
