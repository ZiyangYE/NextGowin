"""Build source/mapped word-array transition systems before any memory_map."""
import argparse,json,re,sys,time,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from audit_official_source_equivalence import formal_library
from ownpr_sdpb_formal import word_model
from gowin_netlist_format import LIBRARY
from split_primitive_models import units
from verific_bridge import convert
from yosys_runtime import run,quote
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser();p.add_argument('--synthesis',type=Path,required=True);p.add_argument('--budget',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,300_000_000);source=a.synthesis.resolve();out=a.out.resolve();out.mkdir(exist_ok=False);start=time.time();budget=load_json(a.budget)
    m=load_json(source/'manifest.json')
    if m['status']!='complete' or sha(source/'mapped.vg')!=m['netlist_sha256']:raise ValueError('Unverified mapped source')
    for file,h in m['rtl_input_sha256'].items():
        if sha(Path(file))!=h:raise ValueError('RTL provenance changed')
    tops=re.findall(r'\s-e\s+(\S+)',(source/'synth.tcl').read_text())
    if len(tops)!=1:raise ValueError('Ambiguous top')
    top=tops[0];gold,bridge=convert((source/'elaborated.v').read_text());(out/'gold.v').write_text(gold);(out/'bridge.json').write_text(json.dumps(bridge,indent=2))
    lib,adapters=formal_library();sdk=LIBRARY.read_text(encoding='utf-8');row=units(sdk)['MULTALU27X18'];dsp=sdk[row['start']:row['end']]
    if dsp.count('wire grstn = GSR.GSRO;')!=1:raise ValueError('Unknown DSP GSR expression')
    dsp=dsp.replace('wire grstn = GSR.GSRO;',"wire grstn = 1'b1;")
    (out/'models.v').write_text(lib+'\n'+word_model()+'\n'+dsp)
    sources={str(f):sha(f) for f in (source/'manifest.json',source/'mapped.vg',source/'elaborated.v',LIBRARY,ROOT/'scripts/ownpr_sdpb_formal.py',ROOT/'scripts/audit_official_source_equivalence.py',Path(__file__))}
    r=dict(status='building',top=top,source_sha256=sources,original_budget=budget,equivalence_proved=False,phases=[])
    def save():r['wall_seconds']=time.time()-start;(out/'manifest.json').write_text(json.dumps(r,indent=2))
    shutil.copyfile(__file__,out/'runner_source.py.txt');shutil.copyfile(ROOT/'scripts/ownpr_sdpb_formal.py',out/'word_model_source.py.txt');save()
    try:
        for role,file in [('gold',out/'gold.v'),('gate',source/'mapped.vg')]:
            remaining=budget['deadline_epoch']-time.time()-90
            if remaining<30:raise TimeoutError('Original round deadline')
            commands=['read_verilog -sv '+quote(out/'models.v'),'read_verilog -sv '+quote(file),'hierarchy -check -top '+top,
                'proc','flatten','opt','memory_share','memory_collect','opt_clean','check -assert','write_json '+quote(out/(role+'_memory.json'))]
            invocation=run(commands,out,role,min(180,remaining));r['phases'].append(dict(role=role,invocation=invocation));save()
            data=load_json(out/(role+'_memory.json'));module=data['modules'][top]
            if any(c['type'].startswith('$mem') and c['type']!='$mem_v2' for c in module['cells'].values()):raise ValueError('Uncollected memory')
            r[role]=dict(cells=len(module['cells']),memories=sum(c['type']=='$mem_v2' for c in module['cells'].values()),json_bytes=(out/(role+'_memory.json')).stat().st_size,sha256=sha(out/(role+'_memory.json')));save()
        r['status']='array_transition_systems_built_not_proved'
    except Exception as error:r.update(status='build_rejected_or_incomplete',error=repr(error));save();raise
    save();print(json.dumps({k:r[k] for k in ('status','gold','gate','wall_seconds')},indent=2))


if __name__=='__main__':main()
