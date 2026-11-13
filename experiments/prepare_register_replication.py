"""Prepare and prove an own-synthesis FF-copy checkpoint experiment."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import time
from register_fanout_replication import replicate,validate_graph,prove_sdk,ROOT
from nextgowin import materialize
from native_boundary_cec import prove
from run_abc_reproduction import sha

ap=argparse.ArgumentParser()
ap.add_argument('--source-flow',type=Path,required=True)
ap.add_argument('--source-json',type=Path,required=True)
ap.add_argument('--out',type=Path,required=True)
ap.add_argument('--clusters',action='store_true')
args=ap.parse_args();start=time.monotonic()
source=args.source_flow.resolve();source_json=args.source_json.resolve();out=args.out.resolve()
out.mkdir(parents=True,exist_ok=False)
parent=json.loads((source/'manifest.json').read_text())
if not parent.get('frontend_only_vendor') or parent.get('own_mapping_source','').startswith('Vendor'):
    raise ValueError('Own RTL synthesis source is required')
before=json.loads(source_json.read_text());working=before;cluster_record=None
if args.clusters:
    from dedicated_cluster_replication import replicate as replicate_clusters
    working,cluster_record=replicate_clusters(before)
    cluster_record['proof']=prove(before,working,out/'cluster_proof')
    print('Clusters',cluster_record['clusters'],'extra LUT slots',cluster_record['added_lut_slots'],flush=True)
after,record=replicate(working)
record['graph_proof']=validate_graph(working,after,record)
if cluster_record:record['cluster_replication']=cluster_record
record['state_proof']=prove_sdk(out/'state_proof')
mapping=out/'04_mapping';mapping.mkdir();p=mapping/'mapped.json';p.write_text(json.dumps(after)+'\n')
resources=out/'03_resources';resources.mkdir()
if (source/'03_resources/dsp_cells.v').exists():shutil.copyfile(source/'03_resources/dsp_cells.v',resources/'dsp_cells.v')
if any(int(c.get('attributes',{}).get('ng_packed_oreg','0'),2) for m in after['modules'].values() for c in m.get('cells',{}).values()):
    # Preserve the existing independently proved OREG authorization verbatim.
    shutil.copyfile(source/'03_resources/dsp_output_packing.json',resources/'dsp_output_packing.json')
    shutil.copytree(source/'03_resources/pack_proof',resources/'pack_proof')
top=json.loads((source/'01_frontend/manifest.json').read_text())['top']
materialize(p,out/'05_netlist',top,source/'02_bridge/vendor_cells.v',optimize_resources=False)
actual=json.loads((out/'05_netlist/final.json').read_text());collapsed=copy.deepcopy(actual)
aliases={}
for row in record['copies']:
    mn=row['module'];m=collapsed['modules'][mn];a=m['cells'][row['source']];b=m['cells'][row['clone']]
    if any(a[k]!=b[k] for k in ('type','parameters','port_directions')):
        raise ValueError('Emitted replica metadata differs')
    aliases.setdefault(mn,{})[b['connections']['Q'][0]]=a['connections']['Q'][0]
for row in record['copies']:
    mn=row['module'];m=collapsed['modules'][mn];a=m['cells'][row['source']];b=m['cells'][row['clone']]
    norm=lambda bs:[aliases.get(mn,{}).get(bit,bit) for bit in bs]
    if any(norm(a['connections'][p])!=norm(bs) for p,bs in b['connections'].items() if p!='Q'):
        raise ValueError('Emitted replica transition differs')
    del m['cells'][row['clone']]
for mn,m in collapsed['modules'].items():
    for c in m.get('cells',{}).values():
        for pin,bs in c['connections'].items():c['connections'][pin]=[aliases.get(mn,{}).get(bit,bit) for bit in bs]
record['emitted_boundary_cec']=prove(before,collapsed,out/'emitted_cec')
record.update(status='proved_emitted_checkpoint',source_flow_manifest_sha256=sha(source/'manifest.json'),
    source_json_sha256=sha(source_json),output_netlist_sha256=sha(out/'05_netlist/mapped.vg'),
    preparation_wall_seconds=time.monotonic()-start,
    scope='Own checkpoint only. No cold runtime or physical QoR claim. The source JSON may be a previously checked own variant.')
(out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
print('Prepared',record['clones'],'copies in',record['preparation_wall_seconds'],'seconds',flush=True)
