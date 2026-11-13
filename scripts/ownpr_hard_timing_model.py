"""Native-only hard-cell matrix model for three BRAM and one bypass-DSP profile.

Values come from independently bound cells, never vendor placement. Matrices
retain the native representation; this is not complete hard-macro STA signoff.
"""
import argparse,json
from collections import Counter
from pathlib import Path
from ownpr_device import ROOT,DLL_SHA
from ownpr_image_storage import sha

NETLIST=ROOT/'results/coopt_vexriscv_mapping_003/05_netlist_retry/final.json'
NETLIST_SHA='c5f75f627a5573190c58d22a7bff2393373acadd5f3738ac64d8848f77b75524'
SOURCES=('native_bram18_full_timing_001','native_bram9_full_timing_001','native_bram36_full_timing_001','native_dsp_full_timing_001')


def read(p):return json.loads(Path(p).read_text())


def profile(cell):
    return json.dumps([cell['type'],{k:v for k,v in cell['parameters'].items() if not k.startswith('INIT_RAM_')}],sort_keys=True)


def expand(values):return [values[0],values[1],values[0],values[1],values[2],values[3],values[2],values[3]]


class HardCellTiming:
    def __init__(self):
        if sha(NETLIST)!=NETLIST_SHA:raise ValueError('Hard timing profile source changed')
        self.cells=read(NETLIST)['modules']['VexRiscv']['cells'];self.profiles={};self.sources=[]
        vg=NETLIST.with_name('mapped.vg')
        for name in SOURCES:
            folder=ROOT/'results'/name;meta=read(folder/'manifest.json');trials=read(folder/'binding_trials.json')
            if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or meta['vendor_pnr_executed'] is not False:
                raise ValueError('Independent hard timing calibration required')
            if sha(vg)!=meta['source_netlist_sha256'] or sha(folder/'probe.vg')!=meta['fixture_sha256'] or meta['fixture_sha256']!=meta['source_netlist_sha256']:
                raise ValueError('Timing profile fixture differs')
            if sha(folder/'binding_trials.json')!=meta['trials_sha256']:raise ValueError('Hard binding evidence changed')
            first=None
            for index,trial in enumerate(trials['records']):
                sub=folder/f'site_{index:03d}'
                if sha(sub/'hard_resource_bindings.json')!=trial['capture_sha256'] or sha(sub/'hard_timing_queries.json')!=trial['timing_sha256']:
                    raise ValueError('Hard timing observation changed')
                name=read(sub/'hard_resource_bindings.json')['instances'][0]['instance'];cell=self.cells[name];data=read(sub/'hard_timing_queries.json')
                if not data['full_data_pins']:raise ValueError('Sampled hard timing is insufficient for this model')
                if first is not None and data['queries']!=first['queries']:raise ValueError('Hard query changes between sites')
                first=data
            if first is None:raise ValueError('Empty hard timing observations')
            key=profile(cell)
            if key in self.profiles:raise ValueError('Duplicate hard timing mode profile')
            record=dict(primitive=cell['type'],queries={(q['source'],q['destination'],q['selector'],q['flag_a'],q['flag_b']):q for q in first['queries']},edges={})
            if cell['type']=='MULTALU27X18':
                if not read(folder/'site_000/timing_graph_build.json')['returned']:raise ValueError('Missing independent DSP graph')
                record['edges']={(e['source'],e['destination'],e['kind']):e['matrices'] for e in first['actual_edges']}
                if len(record['edges'])!=1656:raise ValueError('Incomplete bypass A/B-to-DOUT graph')
            self.profiles[key]=record;self.sources.append(dict(folder=str(folder.relative_to(ROOT)),manifest_sha256=sha(folder/'manifest.json')))
        from ownpr_registered_dsp import calibrated
        self.registered_key,registered,self.registered_data=calibrated(self.cells,profile,vg)
        self.profiles[self.registered_key]=registered
        self.sources.extend(self.registered_data['sources'])

    def matrices(self,cell,source,destination,kind):
        record=self.profiles.get(profile(cell))
        if record is None:raise ValueError('Uncalibrated hard primitive/mode')
        if cell['type']=='MULTALU27X18':
            result=record['edges'].get((source,destination,kind))
            if result is None:raise ValueError('No calibrated DSP arc for these pins/kind')
            return [list(a) for a in result]
        if kind not in (10,20,22,25,27):raise ValueError('Uncalibrated BRAM edge/clock polarity')
        port=destination.split('[',1)[0]
        allowed=(10,) if port.startswith('DO') else (25,27) if port in ('RESET','RESETA','RESETB') else (20,22)
        if kind not in allowed:raise ValueError('BRAM pin/check kind mismatch')
        flag_a=kind in (20,25);result=[]
        for selector in (1,2):
            row=record['queries'].get((source,destination,selector,flag_a,False))
            if row is None or not row['complete']:raise ValueError('Undefined BRAM clock/pin query')
            result.append(expand(row['values']))
        return result


def audit(reference):
    reference=Path(reference);meta=read(reference/'manifest.json');model=HardCellTiming()
    if meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or not meta['vendor_placement_executed'] or not meta['vendor_routing_executed']:
        raise ValueError('Expected explicitly labelled post-STA native reference')
    if sha(reference/'hard_timing_index.json')!=meta['hard_timing_sha256']:raise ValueError('Hard reference timing index changed')
    index=read(reference/'hard_timing_index.json');counts=Counter();instances=[]
    for row in index['instances']:
        file=reference/row['file']
        if sha(file)!=row['sha256']:raise ValueError('Native hard timing edge observation changed')
        data=read(file);cell=model.cells[row['instance']]
        if not data['full_data_pins']:raise ValueError('Expected complete data-port reference')
        record=model.profiles[profile(cell)]
        queries={(q['source'],q['destination'],q['selector'],q['flag_a'],q['flag_b']):q for q in data['queries']}
        if queries!=record['queries']:raise ValueError('Independent/native hard query differs')
        keys=set()
        for edge in data['actual_edges']:
            key=(edge['source'],edge['destination'],edge['kind'])
            if key in keys:raise ValueError('Duplicate native hard timing edge')
            keys.add(key)
            if model.matrices(cell,*key)!=edge['matrices']:raise ValueError('Hard timing model differs from native STA: '+str((row['instance'],key)))
            counts[cell['type']]+=1
        if cell['type']=='MULTALU27X18' and keys!=set(record['edges']):raise ValueError('Incomplete DSP topology comparison')
        instances.append(dict(instance=row['instance'],primitive=cell['type'],edges=len(keys)))
    if len(instances)!=14 or not all(counts[t] for t in ('MULTALU27X18','DPX9B','SDPX9B')):raise ValueError('Incomplete CPU hard-resource comparison')
    return dict(status='independent_hard_cell_matrices_match_native_sta',dll_sha256=DLL_SHA,source_json_sha256=NETLIST_SHA,
        independent_sources=model.sources,reference=str(reference.relative_to(ROOT)),reference_manifest_sha256=sha(reference/'manifest.json'),
        instances=instances,edges_by_primitive=dict(counts),total_edges=sum(counts.values()),profile_count=len({profile(model.cells[r['instance']]) for r in index['instances']}),
        dsp_legacy_scalar_table_used=False,registered_dsp_modes_verified=False,bram_negative_clock_verified=False,
        hard_pulse_width_verified=False,hard_resource_routing_verified=False,whole_design_timing_signoff=False,
        scope='Current positive-clock BRAM profiles and bypass-DSP A/B-to-DOUT matrices. Final hard STA, pulse widths, clocks, routes and production integration are incomplete.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--reference',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from workspace_budget import reserve
    reserve(ROOT,1_000_000);r=audit(a.reference.resolve());a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(r,indent=2))
    print(json.dumps({k:v for k,v in r.items() if k not in ('instances','independent_sources')},indent=2))
