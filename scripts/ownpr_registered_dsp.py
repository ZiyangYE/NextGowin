"""Native calibrated single-OREG DSP profile; no source-equivalence waiver.

Only observed DOUT bits0..33 and CLK0/CE0/RESET0 SYNC are admitted. Cascaded
outputs and other register configurations are rejected. The library is derived
from independent native bindings, never vendor placement/routes.
"""
import copy
import json
from pathlib import Path
from ownpr_device import ROOT,DLL_SHA
from ownpr_image_storage import sha,load_json

FOLDER=ROOT/'results/dsp_register_joint_trial_024'
CASES=('native_oreg_dynamic_ce_001','native_oreg_clock0_site2_001')+tuple(
    f'native_oreg_dynamic_{i}_site{j}' for i in (9407,9408,9409) for j in (0,1))


def calibrated(source_cells,profile_key,source_netlist):
    cell=copy.deepcopy(source_cells['mult_9406.ng_dsp']);cell['parameters']['OREG_CLK']='CLK0'
    key=profile_key(cell);edges={};pins=None;attrs=None;sources=[];observations={}
    for case in CASES:
        folder=FOLDER/case;meta=load_json(folder/'manifest.json');request=load_json(folder/'resource.json')
        if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['vendor_pnr_executed'] is not False:
            raise ValueError('Registered DSP requires independent native evidence')
        if meta['source_netlist_sha256']!=sha(source_netlist):raise ValueError('Registered DSP source netlist differs')
        if not request.get('all_dsp_edges') or not request.get('build_dsp_timing') or not request.get('binding_adapter'):
            raise ValueError('Registered DSP capture lacks graph or binding checks')
        if sha(folder/'probe.vg')!=meta['fixture_sha256'] or sha(folder/'binding_trials.json')!=meta['trials_sha256']:
            raise ValueError('Registered DSP fixture/binding changed')
        mutation=load_json(folder/'fixture_mutation.json')
        if mutation['parameter']!='OREG_CLK' or mutation['old']!='BYPASS' or mutation['new']!='CLK0':
            raise ValueError('Unexpected registered DSP fixture')
        trials=load_json(folder/'binding_trials.json')
        if not trials.get('logical_associations_restored'):raise ValueError('Registered binding did not restore native associations')
        trial=trials['records']
        if len(trial)!=1:raise ValueError('Expected one independently built graph per process')
        bpath=folder/'site_000/hard_resource_bindings.json';tpath=folder/'site_000/hard_timing_queries.json'
        if sha(bpath)!=trial[0]['capture_sha256'] or sha(tpath)!=trial[0]['timing_sha256']:
            raise ValueError('Registered DSP observation changed')
        binding=load_json(bpath)['instances'][0];data=load_json(tpath)
        if not data.get('all_dsp_pin_edges_requested') or not data.get('full_data_pins') or not load_json(folder/'site_000/timing_graph_build.json')['returned']:
            raise ValueError('Registered DSP timing graph was not completely requested/built')
        if binding['instance']!=mutation['instance']:raise ValueError('Captured the wrong registered DSP')
        expected=copy.deepcopy(source_cells[binding['instance']]);expected['parameters']['OREG_CLK']='CLK0'
        if profile_key(expected)!=key:raise ValueError('Registered profile has conflicting parameters')
        _,r,c,_=binding['site'];local={p['pin']:{'direction':p['direction'],
            'nodes':[[a-r,b-c,k] for a,b,k in p['nodes']]} for p in binding['pins']}
        fields=binding['physical_attributes'][:248]
        if len(fields)!=248 or (attrs is not None and attrs!=fields):raise ValueError('Registered mode attributes differ')
        if pins is not None and pins!=local:raise ValueError('Registered pin template is not site invariant')
        attrs,pins=fields,local
        observed={}
        for edge in data['actual_edges']:
            if edge['kind'] not in (10,20,22):continue
            k=(edge['source'],edge['destination'],edge['kind'])
            if k in observed or edge['source']!='CLK[0]':raise ValueError('Duplicate/unexpected registered edge')
            if k in edges and edges[k]!=edge['matrices']:raise ValueError('Registered matrix changes between captures')
            observed[k]=edge['matrices'];edges[k]=edge['matrices']
        instance=binding['instance'];observations.setdefault(instance,[]).append((r,c,observed))
        sources.append({'folder':str(folder.relative_to(ROOT)),'manifest_sha256':sha(folder/'manifest.json')})
    for instance,rows in observations.items():
        if len(rows)!=2 or rows[0][:2]==rows[1][:2] or rows[0][2]!=rows[1][2]:
            raise ValueError('Two-site registered timing comparison missing')
    required={('CLK[0]',f'DOUT[{i}]',10) for i in range(34)}
    required|={('CLK[0]',pin,k) for pin in [*[f'A[{i}]' for i in range(27)],*[f'B[{i}]' for i in range(18)],'CE[0]','RESET[0]'] for k in (20,22)}
    if set(edges)!=required:raise ValueError('Registered timing coverage changed')
    return key,dict(primitive='MULTALU27X18',queries={},edges=edges,registered_oreg=True),dict(
        cell=cell,pins=pins,attributes=attrs,sources=sources)


def unused_control(cell,port,index):
    active={k:v for k,v in cell['parameters'].items() if k.endswith('_CLK') and v!='BYPASS'}
    return active=={'OREG_CLK':'CLK0'} and port in ('CLK','CE','RESET') and index==1 and cell['parameters'].get('OREG_CE')=='CE0' and cell['parameters'].get('OREG_RESET')=='RESET0'


def install_architecture(architecture,source,ties,profile_key):
    key=architecture.timing.registered_key;data=architecture.timing.registered_data;cell=data['cell']
    # Inactive ordinary data/control values come from the same pinned original
    # fixture. The unused second clock/CE/reset selection is handled explicitly.
    old=source['cells']['mult_9406.ng_dsp'];inactive={}
    from ownpr_hard_architecture import bits
    logical={label:(port,index,bit) for label,port,index,bit in bits(old)}
    for label,pin in data['pins'].items():
        port,index,bit=logical[label]
        if not pin['nodes'] and pin['direction']=='In' and bit in ties:inactive[label]={ties[bit]}
    architecture.templates[key]=dict(primitive=cell['type'],shape={p:len(v) for p,v in cell['connections'].items()},
        pins=copy.deepcopy(data['pins']),inactive_values=inactive,registered_oreg=True)
    architecture.mode_attributes[key]=list(data['attributes'])
