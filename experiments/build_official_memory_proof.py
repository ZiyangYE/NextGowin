"""Build array-preserving source/mapped SMT models; never claim equivalence."""
import argparse,json,re,shutil,time
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from audit_official_source_equivalence import formal_library
from gw5a_sdpb_array_model import model,address_certificate
from verific_bridge import convert
from yosys_runtime import run,quote
from ownpr_image_storage import load_json,sha
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('synthesis','out','budget'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();reserve(ROOT,200_000_000);budget=load_json(a.budget)
    if budget['deadline_epoch']-time.time()<240:raise TimeoutError('Original trial deadline')
    a.out=a.out.resolve();a.out.mkdir(exist_ok=False);source=a.synthesis.resolve();meta=load_json(source/'manifest.json')
    if meta['status']!='complete' or sha(source/'mapped.vg')!=meta['netlist_sha256']:raise ValueError('Native provenance mismatch')
    for path,digest in meta['rtl_input_sha256'].items():
        if sha(Path(path))!=digest:raise ValueError('Source changed')
    record=dict(status='building_array_models',equivalence_proved=False,baseline_frozen=False,original_budget=budget,
        source_synthesis_sha256=sha(source/'manifest.json'),sdpb_adapter_experiment=True)
    def save():(a.out/'manifest.json').write_text(json.dumps(record,indent=2))
    save();shutil.copyfile(__file__,a.out/'runner_source.py.txt')
    try:
        gold,bridge=convert((source/'elaborated.v').read_text());(a.out/'gold.v').write_text(gold)
        (a.out/'bridge.json').write_text(json.dumps(bridge,indent=2));library,_=formal_library()
        from gowin_netlist_format import LIBRARY
        from split_primitive_models import units
        sdk=LIBRARY.read_text(encoding='utf-8');item=units(sdk)['MULTALU27X18'];dsp=sdk[item['start']:item['end']]
        if dsp.count('wire grstn = GSR.GSRO;')!=1:raise ValueError('Unknown DSP global-reset model')
        dsp=dsp.replace('wire grstn = GSR.GSRO;',"wire grstn = 1'b1;")
        (a.out/'models.v').write_text(library+'\n'+model()+'\n'+dsp)
        record['address_certificate']=address_certificate()
        tops=re.findall(r'\s-e\s+(\S+)',(source/'synth.tcl').read_text())
        if len(tops)!=1:raise ValueError('One top required')
        top=tops[0];commands=[]
        for role,file in [('gold',a.out/'gold.v'),('gate',source/'mapped.vg')]:
            commands+=['read_verilog -sv '+quote(a.out/'models.v'),'read_verilog -sv '+quote(file),
                'hierarchy -check -top '+top,'proc','flatten','opt','memory_share','memory_collect','opt_clean','check -assert',
                'write_json '+quote(a.out/(role+'_memory.json')),'clk2fflogic','opt_clean','rename '+top+' '+role,'design -stash '+role]
        commands+=['design -copy-from gold gold','design -copy-from gate gate',
            'miter -equiv -ignore_gold_x -make_assert -flatten gold gate memory_miter','hierarchy -check -top memory_miter',
            'opt_clean','write_smt2 -wires '+quote(a.out/'memory_miter.smt2')]
        record['invocation']=run(commands,a.out,'build',min(180,budget['deadline_epoch']-time.time()-60))
        inventory={}
        for role in ('gold','gate'):
            data=load_json(a.out/(role+'_memory.json'));m=data['modules'][top]
            inventory[role]=[dict(name=n,type=c['type'],parameters=c['parameters'],connections=c['connections']) for n,c in m['cells'].items() if c['type'].startswith('$mem')]
        (a.out/'memory_inventory.json').write_text(json.dumps(inventory,indent=2))
        record.update(status='array_models_built_not_proved',memory_counts={k:len(v) for k,v in inventory.items()},
            smt_bytes=(a.out/'memory_miter.smt2').stat().st_size,
            scope='Explicit SDK-derived SDPB arrays and restored RTL arrays; no memory_map or opaque RAM cuts. Array correspondence/initial-state/inductive proof remains required.')
    except Exception as error:record.update(status='array_model_build_rejected',error=repr(error));save();raise
    save();print(json.dumps(record,indent=2))


if __name__=='__main__':main()
