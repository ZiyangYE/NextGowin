"""Cross-check unplaced hard sites and independently rebound DSP endpoints.

This supplies a fail-closed inventory/port model. Fuse configuration, memories,
registered DSP modes, routing and complete STA remain separate acceptance gates.
"""
import argparse,json,re
from collections import Counter
from pathlib import Path
from ownpr_place import sha
from ownpr_device import ROOT,DLL_SHA,Device


def read(p):return json.loads(Path(p).read_text())


def normalized(row):
    _,r,c,_=row['site'];result={}
    for pin in row['pins']:
        name=pin['pin']
        if name in result:raise ValueError('Duplicate hard pin')
        result[name]=dict(direction=pin['direction'],native_pin_type=pin['native_pin_type'],nodes=[[a-r,b-c,i] for a,b,i in pin['nodes']])
    return result


def audit(inventory,reference,probe,netlist):
    inventory,reference,probe,netlist=map(Path,(inventory,reference,probe,netlist));im=read(inventory/'manifest.json');rm=read(reference/'manifest.json');pm=read(probe/'manifest.json')
    if any(x['status']!='captured' or x['dll_sha256']!=DLL_SHA for x in (im,rm,pm)):raise ValueError('Unverified native hard-resource source')
    if im['vendor_pnr_executed'] or pm['vendor_pnr_executed'] or not rm['vendor_placement_executed'] or not rm['vendor_routing_executed']:raise ValueError('Calibration/independent ownership changed')
    for f,digest in ((inventory/'hard_sites.json',im['sites_sha256']),(reference/'hard_resource_bindings.json',rm['hard_resources_sha256']),(probe/'binding_trials.json',pm['trials_sha256'])):
        if sha(f)!=digest:raise ValueError('Hard-resource observation changed')
    sites=read(inventory/'hard_sites.json');reference_rows=read(reference/'hard_resource_bindings.json')['instances'];d=read(netlist)['modules']['VexRiscv'];cells=d['cells'];device=Device()
    if sites['source_fse_sha256']!=device.fse.sha256:raise ValueError('Hard site FSE differs')
    counts=Counter(r['family'] for r in sites['sites']);report=(reference/'probe.rpt.txt').read_text()
    for family,label in (('bsram','BSRAM'),('new_dsp','DSP')):
        m=re.search(r'\b'+label+r'\s*\|\s*\d+/(\d+)',report)
        if not m or int(m[1])!=counts[family]:raise ValueError('Independent site capacity differs from native report')
    memory_sites={tuple(x['site_header']) for x in sites['sites'] if x['family']=='bsram'};dsp_sites={};samples=[];brams=[]
    for row in sites['sites']:
        if row['family']!='new_dsp':continue
        members=[m for m in row['observed_typed_members'] if m['object_class']=='.?AVmultalu27x18@NETLIST@@']
        if len(members)!=1 or members[0]['offset']!=48:raise ValueError('DSP overlay offset not uniform')
        hdr=members[0]['site_header'];key=tuple(hdr)
        if key in dsp_sites:raise ValueError('Duplicate DSP location')
        dsp_sites[key]=row['id']
    wanted={n for n,c in cells.items() if c['type'] in ('DPX9B','SDPX9B','MULTALU27X18')}
    if {r['instance'] for r in reference_rows}!=wanted:raise ValueError('Hard instance coverage differs from input design')
    constants={'0':0,'1':1}
    for c in cells.values():
        if c['type'] in ('VCC','GND'):constants[next(iter(c['connections'].values()))[0]]=int(c['type']=='VCC')
    for row in reference_rows:
        cell=cells[row['instance']]
        if row['logical_class']=='NewDsp':
            if tuple(row['site']) not in dsp_sites or cell['type']!='MULTALU27X18':raise ValueError('Native DSP occupies an unknown independent site')
            if any(v!='BYPASS' for k,v in cell['parameters'].items() if k.endswith('_CLK')):raise ValueError('Registered DSP profile is outside this model')
            for pin in row['pins']:
                m=re.fullmatch(r'(\w+)(?:\[(\d+)\])?',pin['pin'])
                if not m or m[1] not in cell['connections']:raise ValueError('DSP pin label has no logical port')
                index=int(m[2] or 0);bit=cell['connections'][m[1]][index]
                if not pin['nodes'] and pin['direction']=='In' and bit not in constants and m[1]!='CLK':raise ValueError('Missing active DSP input endpoint')
            samples.append(normalized(row))
        elif row['logical_class']=='Bsram':
            if tuple(row['site']) not in memory_sites:raise ValueError('Native BRAM occupies an unknown independent site')
            brams.append(dict(instance=row['instance'],primitive=cell['type'],site=row['site'],pins=len(row['pins']),
                pins_without_nodes=[p['pin'] for p in row['pins'] if not p['nodes']],multi_node_pins=[p for p in row['pins'] if len(p['nodes'])>1],
                nonlocal_pins=[p for p in row['pins'] if any(n[0]!=row['site'][1] or not 0<=n[1]-row['site'][2]<=2 for n in p['nodes'])]))
        else:raise ValueError('Unexpected hard primitive')
    trials=read(probe/'binding_trials.json')
    if not trials['logical_associations_restored'] or trials['vendor_placement_executed'] or trials['vendor_routing_executed']:raise ValueError('Independent DSP binding did not restore')
    for i,trial in enumerate(trials['records']):
        file=probe/f'site_{i:03d}/hard_resource_bindings.json'
        if sha(file)!=trial['capture_sha256']:raise ValueError('Independent DSP endpoint capture changed')
        row=read(file)['instances'][0]
        if tuple(row['site']) not in dsp_sites:raise ValueError('Independent DSP site not enumerated')
        samples.append(normalized(row))
    if len(samples)!=6 or any(s!=samples[0] for s in samples):raise ValueError('DSP physical pin offsets vary across observations')
    active={k:v for k,v in samples[0].items() if v['nodes']}
    if Counter(re.sub(r'\[.*','',n) for n in active)!=Counter(A=27,B=18,DOUT=48):raise ValueError('Unexpected DSP bypass interface')
    memory_profiles=[];byname={r['instance']:r for r in reference_rows}
    for width in (18,9,36):
        folder=ROOT/f'results/native_bram_binding_{width}_001';meta=read(folder/'manifest.json');trials=read(folder/'binding_trials.json')
        if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['vendor_pnr_executed'] or meta['trials_sha256']!=sha(folder/'binding_trials.json') or not trials['logical_associations_restored']:raise ValueError('Unverified independent BRAM binding')
        independent=[]
        for i,t in enumerate(trials['records']):
            file=folder/f'site_{i:03d}/hard_resource_bindings.json'
            if sha(file)!=t['capture_sha256']:raise ValueError('BRAM endpoint observations changed')
            row=read(file)['instances'][0]
            if tuple(row['site']) not in memory_sites:raise ValueError('Independent BRAM site missing from inventory')
            independent.append(normalized(row))
        if len(independent)!=2 or independent[0]!=independent[1]:raise ValueError('BRAM pin offsets differ by tested site')
        native=byname[row['instance']];observed=normalized(native);aliases=[]
        if set(observed)!=set(independent[0]):raise ValueError('BRAM interface pin set changed')
        for pin,value in independent[0].items():
            actual=observed[pin]
            if value==actual:continue
            if pin not in ('CEA','CEB') or value['direction']!='In' or actual['direction']!='In' or value['native_pin_type']!=actual['native_pin_type']:raise ValueError('Unexplained native BRAM endpoint difference')
            net=cells[row['instance']]['connections'][pin];r,c=native['site'][1:3];expected=set()
            for peer in reference_rows:
                if peer['logical_class']!='Bsram':continue
                for name,offset in (('CEA',0),('CEB',1)):
                    if cells[peer['instance']]['connections'].get(name)==net:expected.add((peer['site'][1]-r,peer['site'][2]+offset-c,131))
            actual_points={tuple(n) for n in actual['nodes']};local={tuple(n) for n in value['nodes']}
            if not local<=actual_points<=expected:raise ValueError('BRAM alias escapes its logical CE net')
            aliases.append(dict(pin=pin,logical_net=net[0],local_nodes=value['nodes'],native_shared_nodes=actual['nodes']))
        memory_profiles.append(dict(primitive=cells[row['instance']]['type'],width=width,instance=row['instance'],pins=len(independent[0]),active_local_pins=sum(bool(v['nodes']) for v in independent[0].values()),independent_sites=2,local_pin_template=independent[0],shared_ce_aliases=aliases))
    return dict(status='hard_site_and_bypass_dsp_ports_verified',site_counts=dict(counts),dsp_cross_checked_samples=len(samples),dsp_pin_template=samples[0],dsp_active_pins=len(active),
        dsp_candidate_sites=[list(k) for k in sorted(dsp_sites)],dsp_overlay_member_offset=48,memory_observations=brams,
        independently_bound_memory_profiles=memory_profiles,
        source_sha256={str(f.resolve()):sha(f) for f in (inventory/'hard_sites.json',reference/'hard_resource_bindings.json',probe/'binding_trials.json',netlist)},
        production_packer_enabled=False,configuration_verified=False,hard_resource_sta_verified=False,memory_pin_template_verified=False,device_programmed=False,
        scope='340 memory and 298 DSP site objects; bypass DSP endpoints match six observations. Three BRAM profiles match two independent sites and native observations, including a checked shared-CE alias set. Encoding, mode breadth, routing and STA remain unimplemented.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    r=audit(ROOT/'results/native_hard_site_inventory_001',ROOT/'results/native_vexriscv_hard_resources_001',ROOT/'results/native_dsp_binding_probe_002',ROOT/'results/coopt_vexriscv_mapping_003/05_netlist_retry/final.json')
    a.out.write_text(json.dumps(r,indent=2));print({k:r[k] for k in ('status','site_counts','dsp_cross_checked_samples','dsp_active_pins')})
