"""Check captured native parent edges against FSE switch masks and spatial aliases."""
import argparse,collections,json,struct
from pathlib import Path
from ownpr_device import Device,RoutingGraph,ROOT
from ownpr_fuse_audit import SwitchChoices
from ownpr_lut_configuration import NativeFrameProjection
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve
from ownpr_clock_graph import ClockGraph
from ownpr_clock_configuration import ClockChoices

def audit(folder):
    meta=load_json(folder/'manifest.json');snapshot=load_json(folder/'physical_before.json')
    if meta.get('own_pnr_result') is not False or meta.get('returncode')!=0 or not meta.get('bitstream_requested') or meta['source_netlist_sha256']!=sha(folder/'probe.vg'):raise ValueError('Unverified encoded native capture')
    device=Device();graph=RoutingGraph(device,ROOT/'results/native_rrg_001');choices=SwitchChoices(device);projection=NativeFrameProjection(device);image=projection.read(folder/'probe.fs')
    nodes=snapshot['routed_endpoint_parent_nodes'];pins=snapshot['logical_pin_nodes'];ownership=collections.defaultdict(set);selected={};unsupported=[];mismatches=[];aliases=[];local=[];values={}
    def point(n):return tuple(struct.unpack('<3h',bytes.fromhex(nodes[str(n)][0])))
    def canonical(p):
        r,c,i=p
        if i not in device.names or not(1<=r<=device.rows and 1<=c<=device.cols):return None
        return graph.node(r-1,c-1,device.names[i])
    matched=0;unmatched=0;net_paths=collections.defaultdict(list);special_ownership=collections.defaultdict(set);fixed_longwire=[]
    for net,(driver,sinks) in snapshot['logical_net_connections'].items():
        if not driver or str(driver) not in pins:continue
        sources=set(pins[str(driver)]['nodes'])
        for sink in sinks:
            for endpoint in pins[str(sink)]['nodes']:
                n=endpoint;path=[];seen=set()
                while n and n not in sources:
                    if n in seen or str(n) not in nodes:raise ValueError('Invalid parent tree')
                    seen.add(n);path.append(n);n=nodes[str(n)][1]
                if n not in sources:unmatched+=1;continue
                matched+=1;path.append(n)
                net_paths[net].append([point(n) for n in path])
                for n in path:
                    p=point(n);key=canonical(p)
                    if key is not None:ownership[key].add(driver)
                    else:special_ownership[p].add(driver)
                for child,parent in zip(path,path[1:]):selected[point(parent),point(child)]=net
    clock=ClockGraph(ROOT/'results/native_clock_inventory_005');clock_nets=set();clock_audits=[]
    def check_bits(requirements,origin):
        for bit,value in requirements.items():
            if bit in values and values[bit]!=value:raise ValueError('Inconsistent switch requirements')
            values[bit]=value
            actual=int(image[projection.index(bit)])-48
            if actual!=value:mismatches.append(dict(origin=origin,bit=bit,expected=value,actual=actual))
    for net,paths in net_paths.items():
        if not any(p in clock.by_coordinate and clock.nodes[clock.by_coordinate[p]]['namespace'].startswith('cmux_group_') for path in paths for p in path):continue
        clock_nets.add(net);parents={}
        for path in paths:
            for i,p in enumerate(path):
                parent=path[i+1] if i+1<len(path) else None
                if p in parents and parents[p]!=parent:raise ValueError('Ambiguous clock parents')
                parents[p]=parent
        tree=dict(parents=[dict(node=p,parent=q) for p,q in parents.items()],paths_sink_to_source=paths,topology_sha256=clock.sha256)
        check=clock.audit_paths(paths)
        try:
            if check['status']!='clock_paths_match':raise ValueError('Uncovered native clock edges')
            requirements,details=ClockChoices(device,clock).requirements(tree)
            if details['conflicts']:raise ValueError('Conflicting native clock choices')
            check_bits(requirements,dict(clock_net=net));clock_audits.append(dict(net=net,status='clock_topology_and_bits_checked',bits=len(requirements),topology=check,configuration=details))
        except ValueError as e:clock_audits.append(dict(net=net,status='unresolved_clock_configuration',error=str(e),topology=check))
    for (source,dest),net in selected.items():
        if net in clock_nets:continue
        from ownpr_lw_fixed_profile import fixed as fixed_lw
        if fixed_lw(device,source,dest):
            fixed_longwire.append(dict(source=source,destination=dest,net=net));continue
        sc,dc=canonical(source),canonical(dest)
        if sc is None or dc is None:unsupported.append(dict(source=source,destination=dest,net=net,reason='outside_ordinary_resource_model'));continue
        if sc==dc:
            aliases.append([source,dest]);continue
        if source[:2]!=dest[:2]:
            unsupported.append(dict(source=source,destination=dest,net=net,reason='intertile_nonalias'))
            continue
        r,c,si=source;di=dest[2];typ=device.grid[r-1][c-1];pair=device.names[si],device.names[di]
        pip=(r-1,c-1,*pair);kind='native_switch_table'
        if pair not in choices.template(typ):
            from ownpr_io_default_pip import requirements as default_io
            requirements=default_io(device,choices,pip);kind='calibrated_site_default_pip'
            if requirements is None:unsupported.append(dict(source=source,destination=dest,net=net,reason='not_in_native_ordinary_switch_tables'));continue
        else:requirements=choices.requirements(pip)
        local.append(dict(pip=pip,bits=len(requirements),kind=kind))
        check_bits(requirements,dict(pip=pip))
    conflicts=[dict(resource=k,drivers=sorted(v)) for k,v in ownership.items() if len(v)>1]
    conflicts.extend(dict(special_clock_point=k,drivers=sorted(v)) for k,v in special_ownership.items() if k in clock.by_coordinate and len(v)>1)
    return dict(status='matched_parent_edges_and_fuses_verified' if not(unsupported or mismatches or conflicts or any(c['status']!='clock_topology_and_bits_checked' for c in clock_audits)) else 'partial_parent_fuse_audit',matched_endpoint_paths=matched,unmatched_endpoint_paths=unmatched,
        unique_parent_edges=len(selected),local_edges=len(local),intertile_alias_edges=sum(a[:2]!=b[:2] for a,b in aliases),same_tile_alias_edges=sum(a[:2]==b[:2] for a,b in aliases),configuration_bits=len(values),configuration_mismatches=mismatches,canonical_resource_conflicts=conflicts,clock_audits=clock_audits,
        unsupported=unsupported,unsupported_reasons=dict(collections.Counter(x['reason'] for x in unsupported)),local_edge_records=local,fixed_longwire_edges=fixed_longwire,
        source_sha256={str(p):sha(p) for p in (folder/'physical_before.json',folder/'primitive_bindings.json',folder/'probe.fs',ROOT/'results/native_rrg_001/manifest.json')},fse_sha256=device.fse.sha256,
        clock_topology_sha256=clock.sha256,scope='Paths that reach their declared native driver only. Native FSE ordinary/approved-clock choices and spatial aliases; remaining static/mode rules stay open. Full baseline not accepted.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args();reserve(ROOT,5000000)
    r=audit(a.folder)
    with a.out.open('x') as f:json.dump(r,f,indent=2)
    print(json.dumps({k:v for k,v in r.items() if k not in ('unsupported','local_edge_records','configuration_mismatches','canonical_resource_conflicts','source_sha256')},indent=2))
    if r['configuration_mismatches'] or r['canonical_resource_conflicts']:raise SystemExit(1)
