"""Compare captured real native endpoint paths with own graph and fuse model."""
import argparse,hashlib,json
from pathlib import Path
from ownpr_device import Device,RoutingGraph,ROOT,DLL_SHA
from ownpr_fuse_audit import SwitchChoices
from ownpr_lut_configuration import NativeFrameProjection


def audit(folder):
    folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());path=folder/'native_endpoint_routes.json'
    if meta.get('status')!='captured' or meta.get('dll_sha256')!=DLL_SHA or meta.get('own_pnr_result') is not False or meta.get('open_chip_database_used') is not False:
        raise ValueError('Unverified native endpoint capture')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=meta.get('endpoint_routes_sha256'):raise ValueError('Native endpoint capture changed')
    data=json.loads(path.read_text());device=Device();graph=RoutingGraph(device,ROOT/'results/native_rrg_001')
    choices=SwitchChoices(device);projection=NativeFrameProjection(device);image=folder/'probe.fs';bits=projection.read(image)
    endpoints=0;unmatched=[];edges={};unsupported=[];intrinsic_bad=[];bit_bad=[];implicit=0;fixed=0;checked_bits=set();local=0;intrinsic=0
    for net in data['nets']:
        expected={tuple(n) for n in net['source_nodes']}
        for sink in net['sinks']:
            endpoints+=1;chain=sink['path_sink_to_source']
            if not chain or tuple(chain[-1]) not in expected or sink['status']!='driver_reached':
                unmatched.append(dict(net=net['name'],instance=sink['instance']));continue
            for dest,source in zip(chain,chain[1:]):edges[tuple(source),tuple(dest)]=net['name']
    for (source,dest),net in edges.items():
        sr,sc,si=source;dr,dc,di=dest
        if si not in device.names or di not in device.names or not (1<=sr<=device.rows and 1<=dr<=device.rows and 1<=sc<=device.cols and 1<=dc<=device.cols):
            unsupported.append(dict(source=source,destination=dest,net=net,reason='outside_normal_node_model'));continue
        sn=graph.node(sr-1,sc-1,device.names[si]);dn=graph.node(dr-1,dc-1,device.names[di])
        if source[:2]!=dest[:2]:
            intrinsic+=1
            if sn!=dn:intrinsic_bad.append(dict(source=source,destination=dest,net=net))
            continue
        pip=(sr-1,sc-1,device.names[si],device.names[di]);typ=device.grid[sr-1][sc-1]
        if not graph.check_pip(pip):
            unsupported.append(dict(source=source,destination=dest,net=net,reason='local_edge_missing',tile_type=typ));continue
        local+=1;requirements=choices.requirements(pip)
        literal=any(r['source']==si and r['destination']==di for r in device.connections(typ))
        if not literal:
            if requirements:implicit+=1
            else:fixed+=1
        for bit,value in requirements.items():
            checked_bits.add(bit);observed=int(bits[projection.index(bit)])-48
            if observed!=value:bit_bad.append(dict(pip=pip,bit=bit,expected=value,observed=observed))
    passed=endpoints>0 and not(unmatched or unsupported or intrinsic_bad or bit_bad)
    return dict(status='native_paths_and_choice_bits_matched' if passed else 'incomplete_or_mismatched',endpoint_paths=endpoints,unmatched_endpoints=unmatched,
        unique_parent_edges=len(edges),local_edges_checked=local,intrinsic_edges_checked=intrinsic,implicit_zero_choices_checked=implicit,fixed_arcs_checked=fixed,
        configuration_bits_checked=len(checked_bits),configuration_bit_mismatches=len(bit_bad),configuration_mismatch_examples=bit_bad[:20],
        unsupported_edges=unsupported,intrinsic_mismatches=intrinsic_bad,source_bitstream_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
        source_endpoint_sha256=meta['endpoint_routes_sha256'],own_pnr_result=False,full_bitstream_verified=False,
        scope='Captured native routed-design paths versus own ordinary graph and masked PIP choices. Does not validate unrelated defaults, primitive configuration, STA or an own-generated image.')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);a=ap.parse_args();r=audit(a.folder)
    (a.folder/'route_path_audit.json').write_text(json.dumps(r,indent=2));print(json.dumps({k:v for k,v in r.items() if k not in ('unsupported_edges','intrinsic_mismatches','configuration_mismatch_examples')},indent=2))
    print('unsupported',len(r['unsupported_edges']),'intrinsic mismatches',len(r['intrinsic_mismatches']))
