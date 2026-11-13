"""Validate actual mapped JSON against retained cut proofs before resuming P&R."""
import json
from pathlib import Path
from map_resource_json import extract,readback
from run_abc_reproduction import ROOT,sha
from verific_bridge import convert
from yosys_runtime import YOSYS


def check(out,config_path):
    previous=json.loads((out/'manifest.json').read_text())
    if previous['config_sha256']!=sha(config_path):raise ValueError('Flow configuration changed')
    frontend=json.loads((out/'01_frontend/manifest.json').read_text())
    if frontend['elaborated_sha256']!=sha(out/'01_frontend/elaborated.v'):
        raise ValueError('Frontend checkpoint changed')
    if any(sha(Path(p))!=h for p,h in frontend['inputs'].items()):raise ValueError('Frontend inputs changed')
    exported=(out/'01_frontend/elaborated.v').read_text(encoding='utf-8')
    profile=previous['config'].get('macro_profile')
    if profile:
        from gowin_macros import prepare
        exported,payload,_=prepare(exported,ROOT/profile,ROOT/previous['config']['rao'])
        if payload!=(out/'02_bridge/macro.vg').read_text(encoding='utf-8'):raise ValueError('Macro binding changed')
    text,record=convert(exported)
    if text!=(out/'02_bridge/design.v').read_text(encoding='utf-8') or record['vendor_library_verilog']!=(out/'02_bridge/vendor_cells.v').read_text(encoding='utf-8'):
        raise ValueError('Bridge interpretation changed; rerun upstream synthesis')
    invocation=json.loads((out/'03_resources/infer.invocation.json').read_text())
    if invocation['returncode']!=0 or invocation['binary_sha256']!=sha(YOSYS):raise ValueError('Resource inference tool changed or failed')
    script=out/'03_resources/infer.ys'
    if script.exists() and invocation.get('script_sha256')!=sha(script):raise ValueError('Resource inference script changed')
    dsp=out/'03_resources/dsp_map.json'
    if previous['config'].get('resource_optimizations') is True and not dsp.exists():
        raise ValueError('Optimized resource checkpoint is missing')
    if dsp.exists():
        from gw5a_dsp import primitive_library
        from gowin_netlist_format import EXPECTED
        if json.loads(dsp.read_text())['model_sha256']!=EXPECTED or (out/'03_resources/dsp_cells.v').read_text()!=primitive_library():
            raise ValueError('DSP interface/model checkpoint changed')
    if previous['config'].get('pack_dsp_registers'):
        from dsp_output_register_pack import pack,validate
        folder=out/'03_resources';record=json.loads((folder/'dsp_output_packing.json').read_text())
        for key,name in [('input_sha256','coarse_source.json'),('input_rtlil_sha256','inferred.il'),('packed_sha256','coarse_packed.json')]:
            if sha(folder/name)!=record[key]:raise ValueError('DSP packing source checkpoint changed')
        if record.get('autoidx_restore_sha256') and sha(folder/'restore_autoidx.il')!=record['autoidx_restore_sha256']:
            raise ValueError('Coarse automatic-name counter changed')
        reconstructed,_=pack(json.loads((folder/'coarse_source.json').read_text()),allow_inverted_enable=previous['config'].get('pack_dsp_inverted_enable',False))
        if reconstructed!=json.loads((folder/'coarse_packed.json').read_text()):
            raise ValueError('DSP packing does not follow the admitted uninitialized-state rule')
        coarse=json.loads((folder/'infer_coarse.invocation.json').read_text())
        if coarse['returncode']!=0 or coarse['binary_sha256']!=sha(YOSYS) or coarse['script_sha256']!=sha(folder/'infer_coarse.ys'):
            raise ValueError('DSP coarse inference checkpoint changed or failed')
        validate(json.loads((folder/'resources.json').read_text()),record,folder)
    memory=out/'03_resources/memory_recovery.json'
    if memory.exists():
        for i,row in enumerate(json.loads(memory.read_text())):
            folder=out/'03_resources'/('memory_'+str(i))
            if 'Equivalence successfully proven!' not in (folder/'proof.log').read_text():raise ValueError('Memory recovery proof missing')
            if row['proof']['script_sha256']!=sha(folder/'proof.ys'):raise ValueError('Memory recovery proof script changed')
            for name,digest in row.get('proof_inputs_sha256',{}).items():
                if Path(name).name!=name or sha(folder/name)!=digest:raise ValueError('Memory proof input changed')
            if sha(ROOT/'FPGAFLY_ISCAS2027/rtl/memory/read_tags.v')!=row['source_sha256']:raise ValueError('Recovered memory source changed')
    if previous['config'].get('soft_comparisons',False):
        from lower_carry_only import lower
        from gowin_netlist_format import EXPECTED
        folder=out/'03_resources';record=json.loads((folder/'comparison_lowering.json').read_text())
        for key,name in [('input_sha256','resources_hard.json'),('lowered_sha256','comparison_lowered.json'),('output_sha256','resources_before_choices.json' if previous['config'].get('register_alternatives') else 'resources.json')]:
            if record[key]!=sha(folder/name):raise ValueError('Comparison lowering checkpoint changed')
        if record['implementation_sha256']!=sha(ROOT/'scripts/lower_carry_only.py') or record['native_model_sha256']!=EXPECTED:
            raise ValueError('Comparison lowering implementation/model changed')
        actual,_=lower(json.loads((folder/'resources_hard.json').read_text()))
        if actual!=json.loads((folder/'comparison_lowered.json').read_text()):raise ValueError('Comparison lowering does not match actual original cells')
        if record['optimization']['returncode']!=0 or record['optimization']['script_sha256']!=sha(folder/'comparison.ys'):
            raise ValueError('Comparison optimization script changed or failed')
    if previous['config'].get('register_alternatives'):
        from register_alternatives import add
        folder=out/'03_resources';record=json.loads((folder/'register_choices.json').read_text())
        if record['implementation_sha256']!=sha(ROOT/'scripts/register_alternatives.py'):
            raise ValueError('Register alternatives implementation changed')
        if record['input_sha256']!=sha(folder/'resources_before_choices.json') or record['output_sha256']!=sha(folder/'resources.json'):
            raise ValueError('Register alternatives checkpoint changed')
        actual,recomputed=add(json.loads((folder/'resources_before_choices.json').read_text()))
        if actual!=json.loads((folder/'resources.json').read_text()) or recomputed['choices']!=record['choices']:
            raise ValueError('Register alternatives next-state proof disagrees with original cells')
    mapped=json.loads((out/'04_mapping/manifest.json').read_text())
    if mapped['status']!='complete' or mapped['source_sha256']!=sha(out/'03_resources/resources.json') or mapped['output_sha256']!=sha(out/'04_mapping/mapped.json'):
        raise ValueError('Mapping checkpoint incomplete or changed')
    before=json.loads((out/'03_resources/resources.json').read_text())['modules']
    after=json.loads((out/'04_mapping/mapped.json').read_text())['modules']
    if set(before)!=set(after):raise ValueError('Mapped module set changed')
    if mapped.get('strategy')=='abc9_box':
        from gw5a_box_mapper import scoped_candidate
        from native_boundary_cec import prove
        reference,candidate,_=scoped_candidate(dict(modules=before),dict(modules=after),mapped['mapped_user_modules'])
        if candidate['modules']!=after:raise ValueError('Unexpected change outside ABC9 user scope')
        recorded=json.loads((out/'04_mapping/proof_reference.json').read_text())
        if reference['modules']!=recorded['modules'] or sha(out/'04_mapping/proof_reference.json')!=mapped['proof_reference_sha256']:
            raise ValueError('Actual ABC9 input disagrees with the retained proof reference')
        for name,digest in mapped['implementation_sha256'].items():
            if sha(ROOT/'scripts'/name)!=digest:raise ValueError('Box mapper implementation changed')
        models=mapped['timing_models']
        for path,key in [(ROOT/'results/timing/gowin138_model.json','source_lut_model_sha256'),
                         (ROOT/'results/timing/gowin138_model_ff_checked.json','source_ff_model_sha256'),
                         (ROOT/'configs/gw5a_arch_cost_v2.json','source_cost_sha256')]:
            if sha(path)!=models[key]:raise ValueError('Box mapper timing source changed')
        from yosys_runtime import SUITE
        if sha(SUITE/'share/yosys/gowin/cells_sim.v')!=models['source_sha256']:
            raise ValueError('Upstream primitive timing/functions changed')
        # Recreate the actual hard/state-boundary proof. JSON numeric IDs may
        # differ; checking source text or old LUT names would be insufficient.
        folder=out/'04_mapping'/('resume_native_proof_'+str(len(list((out/'04_mapping').glob('resume_native_proof_*')))))
        prove(recorded,dict(modules=after),folder,300)
        return previous,frontend,mapped
    changed={row['name']:row for row in mapped['modules']}
    for name,old in before.items():
        new=after[name]
        if name not in changed:
            if old!=new:raise ValueError('Unexpected unproved module change')
            continue
        cut=extract(old);row=changed[name];proof=row['readback_cec']
        folder=Path(proof['log']).parent
        actual=readback(new,cut)
        if cut['blif']!=(folder/'source.blif').read_text() or actual!=(folder/'readback.blif').read_text():
            raise ValueError('Actual mapped cells disagree with the proved truth tables')
        if not proof['equivalent'] or proof['reference_sha256']!=sha(folder/'source.blif') or proof['candidate_sha256']!=sha(folder/'readback.blif'):
            raise ValueError('Cut proof artifacts changed')
        if 'Networks are equivalent' not in Path(proof['log']).read_text():raise ValueError('Positive proof log absent')
        old_hard={n:c for n,c in old['cells'].items() if n not in cut['gates']}
        if any(new['cells'].get(n)!=c for n,c in old_hard.items()):raise ValueError('State or hard resource changed')
        if any(n not in old_hard and not n.startswith('ng_acd_') for n in new['cells']):raise ValueError('Unexpected new resource')
        old_meta={k:v for k,v in old.items() if k!='cells'}
        if old_meta!={k:v for k,v in new.items() if k!='cells'}:raise ValueError('Module interface or attributes changed')
    model=ROOT/'results/timing/gowin138_model.json'
    if mapped['model_sha256']!=sha(model):raise ValueError('Mapping timing model changed')
    return previous,frontend,mapped
