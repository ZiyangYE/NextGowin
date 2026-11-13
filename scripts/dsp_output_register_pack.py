"""Propose OREG packing only for an exclusive, uninitialized output FF bank.

The source's undefined initial bits permit the DSP register's initial state.
Defined INIT, reset, preserved state, mixed enables/clocks, cascaded outputs,
and duplicated independent state are excluded. This is not general retiming.
"""
import argparse
from collections import defaultdict
import copy
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_netlist_format import LIBRARY,EXPECTED,formats
from verific_bridge import units
from run_abc_reproduction import sha
from yosys_runtime import run,quote


def value(text):
    if text.startswith('"'):return text.strip('"')
    match=re.fullmatch(r"(\d+)'([bhd])([0-9a-fA-F]+)",text)
    if match:return format(int(match[3],{'b':2,'h':16,'d':10}[match[2]]),'0'+match[1]+'b')
    if text.isdigit():return format(int(text),'032b')
    raise ValueError('Unsupported DSP parameter literal')


def kept(attrs):
    for name in ('keep','dont_touch','syn_preserve','syn_keep'):
        v=attrs.get(name,'0')
        if isinstance(v,int):
            if v:return True
        elif v.lower() not in ('false','no','') and (int(v,2) if set(v)<=set('01') else True):return True
    return False


def pack(source,allow_inverted_enable=False):
    if type(allow_inverted_enable) is not bool:raise ValueError('Inverted-enable admission must be Boolean')
    design=copy.deepcopy(source);rows=[];skipped=defaultdict(int)
    base={k:value(v) for k,v in formats()['MULTALU27X18'].items()};base['C_SEL']='0'
    # The materializer supplies an inactive global GSR. Do not make that
    # assumption for a design with a user-controlled GSR already present.
    for m in design['modules'].values():
        for c in m.get('cells',{}).values():
            if c['type']=='GSR' and c['connections'].get('GSRI')!=['1']:
                return design,dict(status='proposed',schema='gw5a-undefined-state-oreg-pack-v1',packed=[],
                    removed_flipflops=0,skipped={'user_gsr':1},scope=__doc__,model_sha256=EXPECTED)
    for mn,m in design['modules'].items():
        if mn.startswith('~') or int(m.get('attributes',{}).get('blackbox','0'),2):continue
        cells=m.get('cells',{});loads=defaultdict(list);protected=set();state_preserved=set();initialized=set()
        maximum=max((b for c in cells.values() for bs in c['connections'].values() for b in bs if isinstance(b,int)),default=1)
        maximum=max(maximum,max((b for r in list(m.get('ports',{}).values())+list(m.get('netnames',{}).values()) for b in r['bits'] if isinstance(b,int)),default=1))
        inverted_enables={}
        for n,c in cells.items():
            for p,bs in c['connections'].items():
                if c['port_directions'].get(p) in ('input','inout'):
                    for i,b in enumerate(bs):loads[b].append((n,p,i))
        protected.update(b for p in m.get('ports',{}).values() if p['direction']!='input' for b in p['bits'])
        for r in m.get('netnames',{}).values():
            if kept(r.get('attributes',{})):protected.update(r['bits']);state_preserved.update(r['bits'])
            init=r.get('attributes',{}).get('init')
            if init is not None:
                initialized.update(b for b,v in zip(r['bits'],reversed(init)) if v in ('0','1'))
        for name,dsp in list(cells.items()):
            if dsp['type']!='MULTALU27X18':continue
            if kept(dsp.get('attributes',{})):skipped['preserved_dsp']+=1;continue
            if any(k not in base or v!=base[k] for k,v in dsp.get('parameters',{}).items()):
                skipped['non_bypass_configuration']+=1;continue
            if any(loads[b] or b in protected for b in dsp['connections'].get('CASO',[])):
                skipped['cascaded_output']+=1;continue
            bank=[];control=None;reason=None
            for index,bit in enumerate(dsp['connections']['DOUT']):
                sinks=loads[bit]
                if bit in protected:reason='observable_unregistered_output';break
                if not sinks:continue
                if index>=45:reason='outside_proved_product_bits';break
                if len(sinks)!=1:reason='shared_product_output';break
                ff_name,pin,pin_index=sinks[0];ff=cells[ff_name];ps=ff['connections'];params=ff['parameters']
                if pin!='D' or pin_index!=0 or ff['type'] not in ('$dff','$dffe') or int(params['WIDTH'],2)!=1:
                    reason='non_ff_consumer';break
                if kept(ff.get('attributes',{})) or ps['Q'][0] in state_preserved:
                    reason='preserved_state';break
                allowed={'CLK_POLARITY','WIDTH'}|({'EN_POLARITY'} if ff['type']=='$dffe' else set())
                polarity=int(params.get('EN_POLARITY','1'),2)
                if set(params)!=allowed or int(params['CLK_POLARITY'],2)!=1 or polarity not in (0,1) or (polarity==0 and not allow_inverted_enable):
                    reason='unsupported_ff_semantics';break
                if ps['Q'][0] in initialized:reason='defined_initial_state';break
                signature=(ps['CLK'][0],ps['EN'][0] if ff['type']=='$dffe' else '1',polarity)
                if control is None:control=signature
                if signature!=control:reason='mixed_clock_or_enable';break
                bank.append(dict(index=index,name=ff_name,cell=copy.deepcopy(ff)))
            if reason or not bank:
                skipped[reason or 'no_output_state']+=1;continue
            if len({r['cell']['connections']['Q'][0] for r in bank})!=len(bank):
                skipped['aliased_state']+=1;continue
            before=copy.deepcopy(dsp)
            enable=control[1];enable_inverter=None
            if control[2]==0:
                if enable in ('0','1'):enable='1' if enable=='0' else '0'
                else:
                    if enable not in inverted_enables:
                        inv='ng_dsp_oreg_enable_inv_'+str(len(inverted_enables))
                        if inv in cells or inv+'_net' in m.get('netnames',{}):raise ValueError('DSP enable inverter name collision')
                        maximum+=1;cells[inv]=dict(type='$_NOT_',hide_name=0,parameters={},attributes={},
                            connections=dict(A=[enable],Y=[maximum]),port_directions=dict(A='input',Y='output'))
                        m.setdefault('netnames',{})[inv+'_net']=dict(hide_name=0,bits=[maximum],attributes={})
                        inverted_enables[enable]=(maximum,inv)
                    enable,enable_inverter=inverted_enables[enable]
            dsp['parameters']={**base,'OREG_CLK':'CLK0','OREG_CE':'CE0','OREG_RESET':'RESET0'}
            dsp['connections']['CLK']=[control[0],control[0]]
            dsp['connections']['CE']=[enable,enable]
            dsp['connections']['RESET']=['0','0']
            for row in bank:
                dsp['connections']['DOUT'][row['index']]=row['cell']['connections']['Q'][0]
                del cells[row['name']]
            dsp.setdefault('attributes',{})['ng_packed_oreg']='1'
            rows.append(dict(module=mn,instance=name,source_dsp=before,bank=bank,
                             state_relation='Each removed, undefined-initial FF Q equals the corresponding DSP OREG bit. Other OREG bits are unobserved.',
                             clock=control[0],enable=enable,source_enable=control[1],enable_polarity=control[2],enable_inverter=enable_inverter))
    return design,dict(status='proposed',schema='gw5a-undefined-state-oreg-pack-v1',packed=rows,
                       removed_flipflops=sum(len(r['bank']) for r in rows),skipped=dict(skipped),
                       allow_inverted_enable=allow_inverted_enable,scope=__doc__,model_sha256=EXPECTED)


def prove_template(out,negative_control=False,enable_polarity=1):
    if enable_polarity not in (0,1):raise ValueError('Unknown enable polarity')
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    if sha(LIBRARY)!=EXPECTED:raise ValueError('Native DSP model changed')
    text=LIBRARY.read_text(encoding='utf-8');a,b=units(text)['MULTALU27X18'];model=text[a:b]
    if model.count('wire grstn = GSR.GSRO;')!=1:raise ValueError('Unknown GSR model')
    model=model.replace('wire grstn = GSR.GSRO;',"wire grstn = 1'b1;")
    pins=".A(a),.B(b),.C(48'b0),.D(26'b0),.SIA(27'b0),.CASI(48'b0),.PSEL(1'b0),.ASEL(1'b0),.CSEL(1'b0),.CASISEL(1'b0),.PADDSUB(1'b0),.ACCSEL(1'b0),.ADDSUB(2'b0),.CLK({clk,clk}),.CE({ce,ce}),.RESET(2'b0)"
    wrapper='''module miter(input clk,ce,input [26:0] a,input [17:0] b,output equal,
output [44:0] old_observe,packed_observe,product_observe);
wire [47:0] product,packed_q;reg [44:0] old_q;
always @(posedge clk) if(ce) old_q <= product[44:0];
'''
    if enable_polarity==0:wrapper=wrapper.replace('if(ce)','if(!ce)')
    gate_enable='ce' if enable_polarity==1 else '~ce'
    if negative_control:gate_enable='~ce' if enable_polarity==1 else 'ce'
    gate_pins=pins.replace('.CE({ce,ce})','.CE({'+gate_enable+','+gate_enable+'})')
    wrapper+=f"MULTALU27X18 #(.C_SEL(1'b0)) gold({pins},.DOUT(product));\n"+f"MULTALU27X18 #(.C_SEL(1'b0),.OREG_CLK(\"CLK0\")) gate({gate_pins},.DOUT(packed_q));\n"+'assign equal=(old_q==packed_q);endmodule\n'
    wrapper=wrapper.replace('assign equal=(old_q==packed_q);',
        'assign old_observe=old_q;assign packed_observe=packed_q[44:0];assign product_observe=product[44:0];assign equal=(old_q==packed_q[44:0]);')
    source=out/'proof.v';source.write_text(model+'\n'+wrapper)
    run(['read_verilog -sv '+quote(source),'hierarchy -top miter','proc','flatten','opt_expr',
         'write_json '+quote(out/'raw_transition.json')],out,'prepare',90)
    raw=json.loads((out/'raw_transition.json').read_text());module=raw['modules']['miter'];protected=[]
    for signal in ('old_q','gate.out_reg_sync'):
        bits=set(module['netnames'][signal]['bits'])
        drivers=[(n,c) for n,c in module['cells'].items() if c['type'].startswith(('$dff','$adff')) and bits & set(c['connections']['Q'])]
        if len(drivers)!=1:raise ValueError('Could not identify actual SDK register state: '+signal)
        name,cell=drivers[0];cell.setdefault('attributes',{})['keep']='1';protected.append(name)
    (out/'protected_transition.json').write_text(json.dumps(raw)+'\n')
    invocation=run(['read_json '+quote(out/'protected_transition.json'),'opt -full','wreduce','dffunmap','opt_clean',
                    'check -assert','write_json '+quote(out/'transition.json'),
                    'sat -seq 2 -set-at 1 equal 1 -prove-skip 1 -prove equal 1 -verify -timeout 60'],out,'proof',90)
    final=json.loads((out/'transition.json').read_text())['modules']['miter']
    state=[c for c in final['cells'].values() if c['type'].startswith(('$dff','$adff'))]
    if len(state)!=2 or not isinstance(final['ports']['equal']['bits'][0],int):
        raise ValueError('Reject vacuous proof: register states or equality were merged away')
    if any(c['type']!='$dff' or c['connections']['CLK']!=final['ports']['clk']['bits'] or int(c['parameters']['CLK_POLARITY'],2)!=1 for c in state):
        raise ValueError('Unexpected active state or clock in SDK projection')
    if not any(c['type']=='$mul' for c in final['cells'].values()):
        raise ValueError('Reject vacuous proof: the symbolic DSP multiplier disappeared')
    log=(out/'proof.log').read_text()
    if 'SUCCESS!' not in log:raise ValueError('DSP OREG induction step was not proved')
    record=dict(status='proved_transition_refinement',model_sha256=EXPECTED,projection='GSR is inactive, as required by admission.',
        induction='Assume mapped old_q==OREG at step 1; actual SDK models prove equality at step 2 for all A/B/CE.',
        initial_relation='Source FFs must have no defined INIT. Every allowed implementation initial OREG state maps to an allowed source state via selected bits; no equality of independently chosen initial states is assumed.',
        invocation=invocation,proof_source_sha256=sha(source),proof_log_sha256=sha(out/'proof.log'),
        transition_sha256=sha(out/'transition.json'),guard_preserved_state_cells=2,symbolic_product_retained=True,data_bits_proved=45,
        negative_control=negative_control,enable_polarity=enable_polarity)
    (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');return record


def validate(design,record,folder):
    """Authorize only the actual DSP cells covered by a retained packing proof."""
    if record.get('status')!='proved' or record['model_sha256']!=EXPECTED or sha(LIBRARY)!=EXPECTED:
        raise ValueError('DSP packing has no valid pinned-model certificate')
    if record['implementation_sha256']!=sha(Path(__file__).resolve()):
        raise ValueError('DSP packing implementation changed')
    proof=record['transition_proof'];p=Path(folder)/'pack_proof'
    if proof.get('negative_control') or proof.get('guard_preserved_state_cells')!=2 or proof.get('data_bits_proved')!=45 or not proof.get('symbolic_product_retained'):
        raise ValueError('Missing non-vacuous DSP transition proof')
    for name,key in [('proof.v','proof_source_sha256'),('proof.log','proof_log_sha256'),('transition.json','transition_sha256')]:
        if sha(p/name)!=proof[key]:raise ValueError('DSP transition proof artifact changed')
    if 'SUCCESS!' not in (p/'proof.log').read_text():raise ValueError('DSP transition proof did not succeed')
    inverted=[r for r in record['packed'] if r.get('enable_polarity',1)==0]
    if inverted:
        low=record.get('inverted_enable_proof',{});folder_low=Path(folder)/'pack_inverted_enable_proof'
        if low.get('enable_polarity')!=0 or low.get('negative_control') or low.get('guard_preserved_state_cells')!=2 or low.get('data_bits_proved')!=45 or not low.get('symbolic_product_retained'):
            raise ValueError('Missing actual SDK proof for active-low enables')
        for name,key in [('proof.v','proof_source_sha256'),('proof.log','proof_log_sha256'),('transition.json','transition_sha256')]:
            if sha(folder_low/name)!=low[key]:raise ValueError('Inverted-enable proof artifact changed')
        if 'SUCCESS!' not in (folder_low/'proof.log').read_text():raise ValueError('Inverted-enable transition proof failed')
    expected={(r['module'],r['instance']):r['cell'] for r in record['admitted_cells']}
    actual={(mn,n):c for mn,m in design['modules'].items() for n,c in m.get('cells',{}).items()
            if int(c.get('attributes',{}).get('ng_packed_oreg','0'),2)}
    if actual!=expected:raise ValueError('Actual registered DSPs differ from admitted resource cells')
    return set(actual)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('source',type=Path);ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--prove',action='store_true');args=ap.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    candidate,record=pack(json.loads(args.source.read_text()))
    print('Proposed DSPs',len(record['packed']),'FFs',record.get('removed_flipflops',0),record['skipped'],flush=True)
    if args.prove:
        record['transition_proof']=prove_template(out/'template_proof');record['status']='proved'
        (out/'packed.json').write_text(json.dumps(candidate)+'\n')
    record.update(input_sha256=sha(args.source),implementation_sha256=sha(Path(__file__).resolve()))
    (out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
