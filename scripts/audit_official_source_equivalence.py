"""Sequential proof of a checked official elaboration against mapped cells.

Uses installed SDK models, with explicit synthesizable two-state adapters for
its UDP mux and procedural assign/deassign FF simulation constructs. Global
configuration reset is inactive; power-up INIT is retained. Unsupported cells
reject the proof rather than becoming black boxes. This is a source-to-mapped
gate, not a physical-configuration or hardware equivalence certificate.
"""
import argparse,json,re,time,shutil
from pathlib import Path
from gowin_netlist_format import LIBRARY,EXPECTED
from ownpr_image_storage import sha
from split_primitive_models import units
from verific_bridge import convert
from yosys_runtime import ROOT,run,quote
from workspace_budget import reserve


def formal_library():
    if sha(LIBRARY)!=EXPECTED:raise ValueError('Installed SDK model hash changed')
    text=LIBRARY.read_text(encoding='utf-8');index=units(text);parts=[];sources={}
    plain=['INV','IBUF','OBUF','GND','VCC','ALU','GSR']
    plain += [n for n in index if re.fullmatch(r'LUT[1-8]|MUX\d+(?:_\w+)?',n)]
    plain += ['MUX2']
    for name in sorted(set(plain)):
        row=index[name];body=text[row['start']:row['end']]
        parts.append(body);sources[name]=dict(start=row['start'],end=row['end'],adapter=False)
    parts.append('module mux2(output O,input I0,I1,S); assign O=S?I1:I0; endmodule')
    sources['mux2']=dict(adapter=True,identity='SDK binary UDP truth table')
    for name in ('DFFRE','DFFSE','DFFCE','DFFPE'):
        pin={'DFFRE':'RESET','DFFSE':'SET','DFFCE':'CLEAR','DFFPE':'PRESET'}[name]
        value=int(name in ('DFFSE','DFFPE'));async_event=(' or posedge '+pin) if name in ('DFFCE','DFFPE') else ''
        parts.append(f'module {name}(output reg Q,input D,CLK,CE,{pin}); parameter INIT=1\'b{value}; initial Q=INIT; always @(posedge CLK{async_event}) if ({pin}) Q<=1\'b{value}; else if(CE) Q<=D; endmodule')
        row=index[name];sources[name]=dict(start=row['start'],end=row['end'],adapter=True,
            identity='SDK clock enable and synchronous/asynchronous control priority, INIT; GSR inactive')
    return '\n'.join(parts),sources


def proof_commands(gold,gate,models,top,out,hierarchy_separator='.'):
    if hierarchy_separator not in ('.','/'):raise ValueError('Unsupported hierarchy separator')
    commands=[]
    for role,file in [('gold',gold),('gate',gate)]:
        commands += ['read_verilog -sv '+quote(models),'read_verilog -sv '+quote(file),
            'hierarchy -check -top '+top,'proc','write_json '+quote(out/(role+'_hierarchy.json')),'flatten -separator '+hierarchy_separator,'opt','memory_map','opt','check -assert',
            'clk2fflogic','opt','rename '+top+' '+role,'design -stash '+role]
    return commands+['design -copy-from gold gold','design -copy-from gate gate','design -save original_pair',
        'miter -equiv -ignore_gold_x -make_assert -flatten gold gate original_base','hierarchy -check -top original_base','opt',
        'sat -seq 4 -set-init-undef -set-def-inputs -prove-asserts -verify',
        'design -load original_pair','equiv_make gold gate equiv',
        'hierarchy -check -top equiv','design -save induction_input','equiv_miter -assert -undef proof_base',
        'hierarchy -check -top proof_base','flatten','opt',
        'sat -seq 4 -set-init-undef -set-def-inputs -prove-asserts -verify',
        'design -load induction_input','hierarchy -check -top equiv',
        'equiv_simple -seq 4','equiv_induct -seq 4','equiv_status -assert']


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--synthesis',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--post-pnr',type=Path,help='Checked official fixed-layout calibration folder; prove its post-P&R logical Verilog instead of the mapped netlist')
    p.add_argument('--official-cli-post',action='store_true',help='Prove actual gw_sh post-P&R Verilog from a hash-bound official_cli_folder in the synthesis provenance; does not certify physical encoding')
    p.add_argument('--mapped-gold',action='store_true',help='For post-P&R only: prove mapped-to-post-P&R as a separately labelled transitive step')
    p.add_argument('--hierarchy-separator',choices=('.','/'),default='.',help='Uniform flatten naming convention; preserves logic, initialization and the separate original-pair base check')
    p.add_argument('--seconds',type=int,default=150);a=p.parse_args()
    if a.official_cli_post and a.post_pnr:p.error('Choose one post-P&R provenance source')
    if a.mapped_gold and not a.post_pnr:p.error('Mapped Gold requires a post-P&R step')
    reserve(ROOT,20_000_000);a.out=a.out.resolve();a.out.mkdir(exist_ok=False)
    shutil.copyfile(__file__,a.out/'runner_source.py.txt')
    source=a.synthesis.resolve();meta=json.loads((source/'manifest.json').read_text())
    if meta['status']!='complete' or sha(source/'mapped.vg')!=meta['netlist_sha256']:raise ValueError('Official synthesis provenance mismatch')
    gate=source/'mapped.vg';post_sources={}
    if a.official_cli_post:
        cli=Path(meta['official_cli_folder']);mapped=cli/'project/baseline/impl/gwsynthesis/baseline.vg'
        if (cli/'execution.txt').read_text().splitlines()[0]!='code=0' or sha(mapped)!=meta['official_mapped_sha256'] or sha(mapped)!=sha(gate) or sha(cli/'run.tcl')!=meta['official_recipe_sha256']:
            raise ValueError('Official CLI execution/source provenance differs')
        gate=cli/'project/baseline/impl/pnr/baseline.vo'
        post_sources={str(p):sha(p) for p in (gate,mapped,cli/'run.tcl',cli/'execution.txt',cli/'clock.sdc',cli/'fixed.cst')}
    if a.post_pnr:
        post=a.post_pnr.resolve();pm=json.loads((post/'manifest.json').read_text());pr=json.loads((post/'reload_result.json').read_text())
        if pm.get('returncode')!=0 or pm.get('own_pnr_result') is not False or pm.get('source_netlist_sha256')!=sha(gate) or sha(post/'probe.vg')!=sha(gate) or pr['status']!='fixed_layout_sta_calibrated':raise ValueError('Post-P&R source provenance mismatch')
        gate=post/'probe.vo'
        post_sources={str(p):sha(p) for p in (gate,post/'manifest.json',post/'reload_result.json')}
    for file,digest in meta['rtl_input_sha256'].items():
        if sha(Path(file))!=digest:raise ValueError('RTL/project source changed')
    started=time.time();record=dict(status='running',source_synthesis_manifest_sha256=sha(source/'manifest.json'),
        elaborated_sha256=sha(source/'elaborated.v'),mapped_sha256=sha(source/'mapped.vg'),sdk_library_sha256=sha(LIBRARY),
        global_configuration_reset='inactive',scope=__doc__,physical_equivalence_proved=False)
    def save():
        record['wall_seconds']=time.time()-started
        (a.out/'manifest.json').write_text(json.dumps(record,indent=2))
    save()
    try:
        record.update(gate_path=str(gate),gate_sha256=sha(gate),post_pnr_source_sha256=post_sources,post_pnr_logical_proof=bool(a.post_pnr or a.official_cli_post))
        if a.mapped_gold:
            gold=(source/'mapped.vg').read_text();bridge=dict(status='verbatim_official_mapped_gold',source_sha256=sha(source/'mapped.vg'))
        else:gold,bridge=convert((source/'elaborated.v').read_text())
        record['gold_semantic_stage']='official_mapped' if a.mapped_gold else 'checked_source_elaboration'
        record['hierarchy_separator']=a.hierarchy_separator
        (a.out/'gold.v').write_text(gold)
        (a.out/'bridge.json').write_text(json.dumps(bridge,indent=2))
        library,adapters=formal_library();(a.out/'models.v').write_text(library)
        record['model_adapters']=adapters
        tops=re.findall(r'\s-e\s+(\S+)',(source/'synth.tcl').read_text())
        if len(tops)!=1 or not re.fullmatch(r'[A-Za-z_][\w$]*',tops[0]):raise ValueError('Unproved source top module')
        top=tops[0];record['top']=top
        commands=proof_commands(a.out/'gold.v',gate,a.out/'models.v',top,a.out,a.hierarchy_separator)
        record['invocation']=run(commands,a.out,'proof',a.seconds)
        gsr=[];interfaces=[]
        for role in ('gold','gate'):
            modules=json.loads((a.out/(role+'_hierarchy.json')).read_text())['modules']
            interfaces.append({n:dict(direction=r['direction'],width=len(r['bits']),offset=r.get('offset',0),upto=r.get('upto',0)) for n,r in modules[top]['ports'].items()})
            for mn,module in modules.items():
                if int(str(module.get('attributes',{}).get('blackbox','0')),2):raise ValueError('Unmodeled black box: '+mn)
                known={'1'}
                for cell in module.get('cells',{}).values():
                    if cell['type']=='VCC':known.update(cell['connections']['V'])
                for name,cell in module.get('cells',{}).items():
                    if cell['type']=='GSR':
                        if cell['connections']['GSRI'][0] not in known:raise ValueError('Dynamic/unproved global configuration reset')
                        gsr.append(dict(role=role,module=mn,instance=name,constant_inactive=True))
        record['global_reset_audit']=gsr
        if interfaces[0]!=interfaces[1]:raise ValueError('Top interface contract changed')
        record['interface_contract']=interfaces[0]
        if not re.search(r'Found [1-9]\d* \$equiv cells in equiv:',(a.out/'proof.log').read_text()):raise ValueError('Vacuous equivalence proof')
        record.update(status='source_elaboration_to_official_mapping_proved',source_to_elaboration_provenance_checked=True,
            initial_base_steps=4,induction_steps=4,undefined_initial_state_policy='undef-aware comparison; explicit INIT retained',
            implementation_sha256=sha(Path(__file__)),
            input_sha256={name:sha(a.out/name) for name in ('gold.v','models.v','bridge.json')})
        if a.post_pnr or a.official_cli_post:record['status']='source_elaboration_to_official_post_pnr_logic_proved'
        if a.mapped_gold:record['status']='official_mapping_to_post_pnr_logic_proved'
    except Exception as error:
        record.update(status='proof_rejected_or_incomplete',error=repr(error));save();raise
    save();print(json.dumps(dict(status=record['status'],seconds=record['wall_seconds'])))


if __name__=='__main__':main()
