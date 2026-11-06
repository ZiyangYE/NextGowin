"""Expose terminal dedicated carry outputs through a calibrated ALU and LUT.

Mode 2 with I0=I1=I3=0 yields SUM=~CIN; a LUT1 inverter restores CIN.
Uses the existing calibrated constant profile; state and external cycles stay
unchanged. Carry side taps/forks are outside this terminal-only transform.
"""
import argparse,copy,json
from pathlib import Path
from ownpr_carry_chains import extract
from ownpr_device import Device,ROOT
from ownpr_alu_configuration import Mode2Configuration
from ownpr_place import sha
from native_boundary_cec import prove
from workspace_budget import reserve


def materialize(design,top='top'):
    result=copy.deepcopy(design);m=result['modules'][top];cells=m['cells'];contract=extract(m)
    if contract['blockers']:raise ValueError('Terminal carry materialization does not handle carry forks')
    ground=next((c['connections']['G'][0] for c in cells.values() if c['type']=='GND'),None)
    if ground is None:raise ValueError('Requires an existing explicit ground net')
    nextbit=max(b for c in cells.values() for bs in c['connections'].values() for b in bs if isinstance(b,int))+1
    changes=[]
    for chain in contract['chains']:
        if not chain['tail_loads']:continue
        index=len(changes);name=f'ng_carry_terminal_{index}';lut=name+'_inv'
        if name in cells or lut in cells:raise ValueError('Carry materialization name collision')
        old=chain['tail_cout'];tail=chain['cells'][-1];ci,inv,co=range(nextbit,nextbit+3);nextbit+=3
        cells[tail]['connections']['COUT']=[ci]
        cells[name]=dict(type='ALU',parameters={'ALU_MODE':'10'},attributes={},port_directions={p:('output' if p in ('SUM','COUT') else 'input') for p in ('I0','I1','I3','CIN','SUM','COUT')},
            connections={'I0':[ground],'I1':[ground],'I3':[ground],'CIN':[ci],'SUM':[inv],'COUT':[co]})
        cells[lut]=dict(type='LUT1',parameters={'INIT':'01'},attributes={},port_directions={'I0':'input','F':'output'},connections={'I0':[inv],'F':[old]})
        changes.append(dict(tail=tail,old_cout_net=old,alu=name,inverter=lut,added_alu=1,added_lut=1,original_length=chain['length']))
    return result,changes


def materialize_heads(design,top='top'):
    """Seed an ordinary dynamic carry input through a calibrated mode-2 ALU.

    I0=I3=1, CIN=0 gives COUT=I1; its SUM is unused. This preserves the
    original carry chain and cycles instead of treating CIN as routable fabric.
    """
    result=copy.deepcopy(design);m=result['modules'][top];cells=m['cells'];chains=extract(m)
    if chains['blockers']:raise ValueError('Dynamic carry head normalization does not handle forks')
    changes=[]
    zero=next(c['connections']['G'][0] for c in cells.values() if c['type']=='GND')
    one=next(c['connections']['V'][0] for c in cells.values() if c['type']=='VCC')
    nextbit=max(b for c in cells.values() for bs in c['connections'].values() for b in bs if isinstance(b,int))+1
    for chain in chains['chains']:
        if chain['head_constant'] is not None:continue
        name='ng_carry_dynamic_head_'+str(len(changes))
        if name in cells:raise ValueError('Dynamic carry seed name collision')
        head=chain['cells'][0];old=chain['head_cin'];co,unused=nextbit,nextbit+1;nextbit+=2
        cells[name]=dict(type='ALU',parameters={'ALU_MODE':'10'},attributes={},port_directions={p:('output' if p in ('SUM','COUT') else 'input') for p in ('I0','I1','I3','CIN','SUM','COUT')},
            connections={'I0':[one],'I1':[old],'I3':[one],'CIN':[zero],'SUM':[unused],'COUT':[co]})
        cells[head]['connections']['CIN']=[co];changes.append(dict(head=head,seed=name,dynamic_bit=old,added_alu=1))
    if changes:Mode2Configuration(Device()).word(2,[1,'signal',1,0])
    return result,changes


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--netlist',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,50_000_000);a.out.mkdir(exist_ok=False);before=json.loads(a.netlist.read_text());after,changes=materialize(before)
    model=Mode2Configuration(Device());word=model.word(2,[0,0,0,'signal'])
    proof=prove(before,after,a.out/'cec',120);(a.out/'netlist.json').write_text(json.dumps(after,indent=2))
    result=dict(status='equivalent_terminal_carry_materialization',changes=changes,proof=proof,calibrated_truth_word=word,
        input_sha256=sha(a.netlist),output_sha256=sha(a.out/'netlist.json'),interface_cycles_preserved=True,physical_legality_verified=False,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='proof'},indent=2))
