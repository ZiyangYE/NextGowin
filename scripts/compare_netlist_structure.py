"""Compare mapped structural JSON at a common opaque-IP boundary."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
from analyze_route_fanout import analyze
from resource_preflight import count


def scope(design,opaque):
    modules=dict(design['modules'])
    for name in opaque:
        if name not in modules:raise ValueError('Missing common opaque boundary: '+name)
        module=dict(modules[name]);module['cells']={}
        module['attributes']={**module.get('attributes',{}),'blackbox':'1'};modules[name]=module
    return dict(design,modules=modules)


def features(design,top):
    modules=design['modules'];multiplicity=Counter();dsp_paths=[]
    def walk(name,path,stack):
        if name in stack:raise ValueError('Recursive hierarchy')
        multiplicity[name]+=1
        for n,c in modules[name].get('cells',{}).items():
            child=modules.get(c['type'])
            if child and not int(child.get('attributes',{}).get('blackbox','0'),2):walk(c['type'],path+(n,),stack+(name,))
            elif c['type'].startswith(('MULT','DSP')):dsp_paths.append(dict(instance='/'.join(path+(n,)),type=c['type']))
    walk(top,(),())
    carry=Counter();alu_modes=Counter();module_rows=[];ff_groups=[];lut1=Counter()
    for name,times in multiplicity.items():
        cells=modules[name].get('cells',{});alus={n:c for n,c in cells.items() if c['type']=='ALU'}
        initial={}
        for net in modules[name].get('netnames',{}).values():
            value=net.get('attributes',{}).get('init')
            if value is None:continue
            if len(value)!=len(net['bits']):raise ValueError('Unexpected initial-value width')
            for bit,value in zip(net['bits'],reversed(value)):
                if bit in initial and initial[bit]!=value:raise ValueError('Conflicting initial-value aliases')
                initial[bit]=value
        groups=defaultdict(list)
        for cname,cell in cells.items():
            if cell['type'].startswith('DFF'):
                signature=json.dumps([cell['type'],cell.get('parameters',{}),{p:b for p,b in cell['connections'].items() if p!='Q'},
                    [initial.get(b) for b in cell['connections'].get('Q',[])]],sort_keys=True)
                groups[signature].append(cname)
            if cell['type']=='LUT1':lut1[str(int(cell['parameters'].get('INIT','0'),2))]+=times
        for signature,names in groups.items():
            if len(names)>1:ff_groups.append(dict(module=name,instances=times,copies=len(names),names=names,signature=json.loads(signature)))
        outputs={tuple(c['connections'].get('COUT',[])):n for n,c in alus.items() if c['connections'].get('COUT')}
        before={n:outputs.get(tuple(c['connections'].get('CIN',[]))) for n,c in alus.items()}
        after={p:n for n,p in before.items() if p is not None}
        seen=set()
        for start in alus:
            if before[start] is not None:continue
            length=0;here=start
            while here is not None:
                if here in seen:raise ValueError('Branched/cyclic carry chain unsupported')
                seen.add(here);length+=1;here=after.get(here)
            carry[length]+=times
        if set(alus)-seen:raise ValueError('Cyclic/unaccounted carry cells')
        for c in alus.values():alu_modes[str(int(c['parameters'].get('ALU_MODE','0'),2))]+=times
        local=Counter(c['type'] for c in cells.values() if c['type']!='$scopeinfo')
        if alus or any(k.startswith('MULT') for k in local):
            module_rows.append(dict(module=name,instances=times,direct_alu=len(alus),direct_dsp={k:v for k,v in local.items() if k.startswith(('MULT','DSP'))}))
    root=modules[top];subtrees=[]
    for name,c in root.get('cells',{}).items():
        child=modules.get(c['type'])
        if child and not int(child.get('attributes',{}).get('blackbox','0'),2):
            subtrees.append(dict(instance=name,module=c['type'],resources=count(design,c['type'])))
    return dict(resources=count(design,top),module_multiplicities=dict(multiplicity),arithmetic_modules=module_rows,
        alu_modes=dict(alu_modes),local_carry_chain_lengths={str(k):v for k,v in sorted(carry.items())},
        same_input_ff_groups=ff_groups,same_input_ff_extra_cells=sum((r['copies']-1)*r['instances'] for r in ff_groups),
        ff_group_scope='Same module, primitive type, parameters, observed Q-wire initializers, and identical non-Q bit connections. This is structural identity, not attribution of every duplicate to fanout optimization.',
        lut1_truth_histogram=dict(lut1),
        carry_scope='Within each module; hierarchy-crossing carry chains may be split. Counts weighted by expanded instances.',
        dsp_instances=dsp_paths,root_subtrees=subtrees)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline',type=Path,required=True);ap.add_argument('--candidate',type=Path,required=True)
    ap.add_argument('--top',default='thefly_top');ap.add_argument('--opaque',action='append',default=['D3_320']);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    result=dict(scope='Structural comparison at identical opaque module boundaries. Not RTL/sequence equivalence, physical allocation, or a matched runtime benchmark.',opaque=args.opaque,cases={})
    for label,path in [('baseline',args.baseline),('candidate',args.candidate)]:
        raw=path.read_bytes();design=scope(json.loads(raw),args.opaque)
        feature=features(design,args.top);feature.update(input=str(path.resolve()),input_sha256=hashlib.sha256(raw).hexdigest())
        (out/(label+'.features.json')).write_text(json.dumps(feature,indent=2)+'\n')
        fanout=analyze(design,args.top);fanout.update(input_sha256=feature['input_sha256'],opaque_modules=args.opaque)
        (out/(label+'.fanout.json')).write_text(json.dumps(fanout,indent=2)+'\n')
        counts=feature['resources']['leaf_cells'];luts=sum(v for k,v in counts.items() if k.startswith('LUT'))
        summary=dict(expanded_leaf_cells=feature['resources']['expanded_leaf_count'],lut=luts,alu=counts.get('ALU',0),
            ff=sum(v for k,v in counts.items() if k.startswith('DFF')),dsp_primitives=len(feature['dsp_instances']),
            wide_mux=sum(v for k,v in counts.items() if k.startswith('MUX2_LUT')),
            lut1_identity=feature['lut1_truth_histogram'].get('2',0),
            same_input_ff_extra_cells=feature['same_input_ff_extra_cells'],
            bsram=sum(v for k,v in counts.items() if k.startswith(('SDP','DP','SP','ROM')) and k not in ('ROM16',)),
            high_fanout_nonclock=fanout['high_fanout_nonclock_nets'],
            high_fanout_bins={str(n):sum(r['sink_pins']>=n and not r['roles'].get('clock') for r in fanout['nets']) for n in (64,128,256,512,1024)})
        result['cases'][label]=summary;print(label,json.dumps(summary))
    (out/'comparison.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
