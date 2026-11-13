import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from prepare_internal_state_cache import transform,synchronous_fanout,verify_materialized
from ownpr_image_storage import load_json


def test_real_cached_control_has_inductive_next_term_and_exact_fold_identity():
    p=ROOT/'results/vexriscv_existing_state_alias_place_002';d=load_json(p/'packed.json');l=load_json(p/'placement.json')
    new,placed,a,b,r=transform(d,l,'ng_coopt_78','ng_output_state_5')
    assert a==b and r['step_vectors']==128 and len(r['rewired_sinks'])==5
    assert placed['fixed_io']==l['fixed_io'] and len(new['modules']['top']['cells'])==len(d['modules']['top']['cells'])+1
    assert verify_materialized(d,new,l,placed,r)['physical_control_groups_checked']
    for pin in ('D','CLK','CE','RESET','Q'):
        bad=copy.deepcopy(new);bad['modules']['top']['cells'][r['new_ff']]['connections'][pin]=['x']
        with pytest.raises(ValueError,match='proven construction'):verify_materialized(d,bad,l,placed,r)
    bad=copy.deepcopy(new);bad['modules']['top']['cells'][r['new_ff']]['parameters']['INIT']='1'
    with pytest.raises(ValueError,match='proven construction'):verify_materialized(d,bad,l,placed,r)
    for n,c in d['modules']['top']['cells'].items():
        if c['type'].startswith('DFF'):assert new['modules']['top']['cells'][n]==c
    with pytest.raises(ValueError,match='next-state term'):transform(d,l,'ng_coopt_78','ng_coopt_78')
    bad=copy.deepcopy(d);bad['modules']['top']['cells']['ng_output_state_5']['parameters']['INIT']='00000000'
    with pytest.raises(ValueError,match='next-state term'):transform(bad,l,'ng_coopt_78','ng_output_state_5')


def test_clock_and_asynchronous_consumers_are_rejected():
    def ff(t,p):return dict(type=t,attributes={},connections={p:[10],'CLK':[1]},port_directions={p:'input','CLK':'input'})
    d={'modules':{'top':{'cells':{'f':ff('DFFCE','CLEAR')}}}}
    with pytest.raises(ValueError):synchronous_fanout(d,10,1)
    d['modules']['top']['cells']['f']=dict(type='DFFRE',attributes={},connections={'CLK':[10]},port_directions={'CLK':'input'})
    with pytest.raises(ValueError,match='clock'):synchronous_fanout(d,10,1)
    for typ in ('MULTALU27X18','DPX9B','SDPX9B'):
        d['modules']['top']['cells']['f']=dict(type=typ,attributes={},connections={'D':[10],'CLK':['0'],'Q':[20]},port_directions={'D':'input','CLK':'input','Q':'output'})
        with pytest.raises(ValueError,match='Unsupported'):synchronous_fanout(d,10,1)
