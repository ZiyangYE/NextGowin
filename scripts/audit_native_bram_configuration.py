"""Independent BRAM association/mode/initialization differential audit."""
import argparse,json
from pathlib import Path
from audit_native_hard_encoding import audit_capture,read
from audit_native_encoding_probe import payload
from ownpr_device import ROOT,Device,DLL_SHA
from ownpr_image_storage import sha
from ownpr_lut_configuration import NativeFrameProjection


def audit():
    d=Device();projection=NativeFrameProjection(d)
    reference=ROOT/'results/native_vexriscv_hard_resources_004';manifest=read(reference/'manifest.json')
    if manifest['status']!='captured' or manifest['dll_sha256']!=DLL_SHA or not manifest['vendor_placement_executed'] or not manifest['vendor_routing_executed']:
        raise ValueError('Explicit native reference required')
    if sha(reference/'hard_resource_bindings.json')!=manifest['hard_resources_sha256']:raise ValueError('Native reference changed')
    native={x['instance']:x for x in read(reference/'hard_resource_bindings.json')['instances']}
    memories=[x for x in native.values() if x['logical_class']=='Bsram']
    if len(memories)!=10 or not all(x['encoder_typed_cache_matches'] and x['encoder_typed_cache_class']=='.?AVBsram@NETLIST@@' for x in memories):
        raise ValueError('Native BRAM typed association is not uniform')
    folders=['native_bram_typed_encoding_001','native_bram_typed_width9_001','native_bram_typed_sdp36_001','native_bram_typed_readreg_001','native_bram_binding_adapter_001','native_dsp_binding_adapter_002']
    captures=[];attribute_checks=[]
    for name in folders:
        folder=ROOT/'results'/name;capture=audit_capture(folder,projection);captures.append(capture)
        a,b=capture['observations']
        if not a['changed_bits'] or a['local_changes']!=b['local_changes']:raise ValueError('Hard mode does not translate between locations')
        if name in folders[:3]:
            for i in range(2):
                row=read(folder/f'site_{i:03d}'/'hard_resource_bindings.json')['instances'][0]
                if not row['encoder_typed_cache_matches'] or row['physical_attributes']!=native[row['instance']]['physical_attributes']:
                    raise ValueError('Independent BRAM state differs from routed native state')
                attribute_checks.append(dict(instance=row['instance'],site=row['site'],attribute_count=len(row['physical_attributes'])))
    baseline=set(map(tuple,captures[0]['observations'][0]['local_changes']));registered=set(map(tuple,captures[3]['observations'][0]['local_changes']))
    if not baseline<registered or len(registered-baseline)!=1:raise ValueError('Expected isolated read-register mode bit')
    mutation=read(ROOT/'results'/folders[3]/'fixture_mutation.json')
    if mutation['parameter']!='READ_MODE0' or mutation['new']!="1'b1" or any(o['instance']!=mutation['instance'] for o in captures[3]['observations']):
        raise ValueError('Read-register fixture identity differs')
    if captures[4]['observations'][0]['local_changes']!=captures[0]['observations'][0]['local_changes']:raise ValueError('Adapter changes BRAM configuration')
    init=[];default=ROOT/'results/native_bram_binding_adapter_001'
    for name,bit in [('native_bram_binding_adapter_002',0),('native_bram_init72_adapter_001',72),('native_bram_initlast_adapter_001',18431)]:
        folder=ROOT/'results'/name;capture=audit_capture(folder,projection);captures.append(capture)
        mutation=read(folder/'fixture_mutation.json')
        if mutation['parameter']!=f'INIT_RAM_{bit//288:02X}' or int(mutation['new'].split("'h")[1],16)!=1<<(bit%288):raise ValueError('Initialization fixture differs')
        for index,observation in enumerate(capture['observations']):
            if observation['local_changes']!=captures[0]['observations'][index]['local_changes'] or observation['instance']!=mutation['instance']:
                raise ValueError('Initialization mutation changed ordinary configuration/instance')
            before=payload(default/f'variant_{index:03d}.fs.gz');after=payload(folder/f'variant_{index:03d}.fs.gz')
            if len(before)!=len(after) or any(len(a)!=len(b) for a,b in zip(before,after)):raise ValueError('Initialization changed payload shape')
            changes=[dict(line=i,width=len(a),bits=[j for j,(u,v) in enumerate(zip(a,b)) if u!=v]) for i,(a,b) in enumerate(zip(before,after)) if a!=b]
            if len(changes)!=1 or changes[0]['width']!=496:raise ValueError('Expected one isolated BRAM tail frame')
            data_bits=[b for b in changes[0]['bits'] if b<432]
            if len(data_bits)!=1 or any(b>=448 for b in changes[0]['bits']):raise ValueError('Unexpected initialization frame/padding change')
            context=read(folder/f'site_{index:03d}'/'encoding_context.json')
            if not context['overlapping_binding_rejected']:raise ValueError('Adapter overlap guard not exercised')
            init.append(dict(source_bit=bit,site=observation['site'],data_bit=data_bits[0],tail_frame_change=changes[0]))
    return dict(status='bram_configuration_differentials_audited',dll_sha256=DLL_SHA,fse_sha256=d.fse.sha256,
        native_typed_cache_samples=len(memories),independent_attribute_checks=attribute_checks,captures=captures,
        read_mode0_one_bit=sorted(registered-baseline),initialization_samples=init,
        binding_adapter_sha256=sha(ROOT/'scripts/native_hard_binding.py'),
        all_initialization_bits_verified=False,tail_crc_algorithm_verified=False,
        hard_resource_sta_verified=False,production_packer_enabled=False,full_bitstream_verified=False,
        scope='Three VexRiscv BRAM profiles, two sites, isolated READ_MODE0 and three initialization bits. Native reference is calibration only; no routed own-CPU signoff.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from workspace_budget import reserve
    reserve(ROOT,1_000_000);result=audit();a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('captures','independent_attribute_checks')},indent=2))
