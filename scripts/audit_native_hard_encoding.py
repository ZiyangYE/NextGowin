"""Audit isolated hard-resource encoding signals, never whole-design signoff."""
import argparse,bisect,json
from pathlib import Path
import numpy as np
from ownpr_device import Device,DLL_SHA,ROOT
from ownpr_image_storage import sha
from ownpr_lut_configuration import NativeFrameProjection
from audit_native_encoding_probe import payload


def read(path):return json.loads(Path(path).read_text())


def local_changes(projection,case):
    result=[]
    for change in case['changes']:
        x,reverse_y=divmod(change['index'],projection.data_width)
        y=projection.data_width-1-reverse_y
        r=bisect.bisect_right(projection.ys,y)-1;c=bisect.bisect_right(projection.xs,x)-1
        if not 0<=r<projection.device.rows or not 0<=c<projection.device.cols:raise ValueError('Change outside device tiles')
        result.append([r+1-case['site'][0],c+1-case['site'][1],y-projection.ys[r],x-projection.xs[c],change['before'],change['after']])
    return sorted(result)


def audit_capture(folder,projection):
    folder=Path(folder);manifest=read(folder/'manifest.json');request=read(folder/'resource.json')
    if manifest['status']!='captured' or manifest['dll_sha256']!=DLL_SHA or manifest['vendor_pnr_executed'] is not False:
        raise ValueError('Independent encoding provenance required')
    for file,key in [('probe.vg','fixture_sha256'),('binding_trials.json','trials_sha256'),('encoding_trials.json','encoding_trials_sha256')]:
        if sha(folder/file)!=manifest[key]:raise ValueError('Encoding evidence changed: '+file)
    binding=read(folder/'binding_trials.json');cases=read(folder/'encoding_trials.json')
    if len(cases)!=2 or len(binding['records'])!=2 or cases[0]['site']==cases[1]['site']:raise ValueError('Two distinct locations required')
    observed=[]
    for index,(case,bound) in enumerate(zip(cases,binding['records'])):
        child=folder/f'site_{index:03d}'
        if bound['site']!=case['site'] or sha(child/'hard_resource_bindings.json')!=bound['capture_sha256']:raise ValueError('Site binding changed')
        context=read(child/'encoding_context.json')
        registered=request.get('register_grid',False)
        count=context.get('grid_logical_count')
        legacy_control=count is None and not registered and 'addInstToGrid' not in (folder/'source.py.txt').read_text()
        if context['site']!=case['site'] or not context['grid_parent_matches'] or (not legacy_control and count!=int(registered)):
            raise ValueError('Grid membership differs from request')
        before=projection.read(folder/case['baseline']);variant=projection.read(folder/case['variant']);restored=projection.read(folder/case['restored'])
        indices=np.flatnonzero(before!=variant)
        actual=[dict(index=int(i),before=int(before[i])-48,after=int(variant[i])-48) for i in indices]
        base_payload=payload(folder/case['baseline']);variant_payload=payload(folder/case['variant'])
        if actual!=case['changes'] or len(actual)!=case['changed_bits']:raise ValueError('Recorded frame differential differs from image')
        if not np.array_equal(before,restored) or payload(folder/case['restored'])!=base_payload or not case['full_payload_restored']:
            raise ValueError('Unbinding did not restore complete payload')
        if (variant_payload!=base_payload)!=case['full_payload_changed']:raise ValueError('Recorded payload differential differs')
        instance=read(child/'hard_resource_bindings.json')['instances'][0]['instance']
        observed.append(dict(site=case['site'],instance=instance,changed_bits=len(actual),local_changes=local_changes(projection,case),
            full_payload_changed=variant_payload!=base_payload,payload_shape_changed=case['payload_shape_changed'],
            image_sha256={k:sha(folder/case[k]) for k in ('baseline','variant','restored')}))
    return dict(folder=str(folder.relative_to(ROOT)),resource=request['resource'],register_grid=request.get('register_grid',False),probe_source_sha256=sha(folder/'source.py.txt'),observations=observed)


def audit():
    device=Device();projection=NativeFrameProjection(device)
    paths=['native_dsp_attribute_context_001','native_dsp_grid_bypass_001','native_dsp_grid_encoding_001','native_bram_attribute_context_001']
    captures=[audit_capture(ROOT/'results'/name,projection) for name in paths]
    missing,bypass,registered,bram=captures
    if any(x['changed_bits'] or x['full_payload_changed'] for x in missing['observations']):raise ValueError('Missing-membership control changed')
    for capture in (bypass,registered):
        a,b=capture['observations']
        if not a['changed_bits'] or a['local_changes']!=b['local_changes']:raise ValueError('DSP difference does not translate across sites')
    mutation=read(ROOT/'results'/paths[2]/'fixture_mutation.json')
    if mutation['parameter']!='OREG_CLK' or mutation['new']!='CLK0' or not mutation['ce_forced_one']:raise ValueError('Unexpected combined mode fixture')
    if any(x['instance']!=mutation['instance'] for x in registered['observations']):raise ValueError('Mode change did not affect bound instance')
    base=set(map(tuple,bypass['observations'][0]['local_changes']));mode=set(map(tuple,registered['observations'][0]['local_changes']))
    if not base<mode:raise ValueError('Expected nonempty combined-mode difference')
    return dict(status='isolated_encoding_differentials_audited',dll_sha256=DLL_SHA,fse_sha256=device.fse.sha256,
        captures=captures,dsp_bypass_changes=sorted(base),dsp_combined_oreg_clk0_ce_one_extra_changes=sorted(mode-base),
        combined_mode_bits_individually_decoded=False,bram_mode_and_initialization_decoded=False,
        hard_configuration_verified=False,hard_resource_sta_verified=False,production_packer_enabled=False,
        conclusion='DSP grid membership enables a reversible, site-translatable configuration signal. This does not prove full DSP semantics or routed hardware operation.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    from workspace_budget import reserve
    reserve(ROOT,1_000_000);result=audit();args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='captures'},indent=2))
