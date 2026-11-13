import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json
from ownpr_pack_wide_ff import transform
from ownpr_wide_ff_packing import pairs
from ownpr_ff_configuration import compile_initial
from ownpr_mux_configuration import compile_wide
from ownpr_mux_macros import validate
from ownpr_device import Device,RoutingGraph
from ownpr_route_design import bind
from ownpr_router import route,validate as validate_routes


@pytest.fixture(scope='module')
def candidate():
    base=ROOT/'results/tmds_fast_ff_inputs_001/placement';d=load_json(base/'packed.json');p=load_json(base/'placement.json')
    new,np,change=transform(d,p,'ng_ownpr_pass_4')
    return d,p,new,np,change


def test_absorption_preserves_every_ff_and_io_and_merges_modes(candidate):
    before,p,new,np,change=candidate;cells=new['modules']['top']['cells'];old=before['modules']['top']['cells']
    assert len(cells)==len(old)-1 and 'ng_ownpr_pass_4' not in cells
    assert new['modules']['top']['ports']==before['modules']['top']['ports']
    assert np['fixed_io']==p['fixed_io']
    assert {n:c for n,c in cells.items() if c['type'].startswith('DFF')}=={n:c for n,c in old.items() if c['type'].startswith('DFF')}
    assert 'ng_ownpr_pass_4' in old and 'wide_ff_root' not in p['clusters']['ng_coopt_404']
    device=Device();pair=pairs(new,np,device)[0];key=pair['row'],pair['column']
    ordinary=next(s for s in compile_initial(new,np)['sites'] if (s['row'],s['column'])==key)
    wide=next(s for s in compile_wide(new,np,device)['sites'] if (s['row'],s['column'])==key)
    assert ordinary['groups']==wide['groups']==pair['groups']
    assert change['clock_replan_required'] and change['lut_growth']==-1


def test_real_bound_ff_uses_di_and_selected_pip(candidate):
    _,_,d,p,_=candidate;device=Device();graph=RoutingGraph(device,ROOT/'results/native_rrg_001')
    nets,_=bind(d,p,graph);pair=pairs(d,p,device)[0];r,c,s=pair['row'],pair['column'],pair['slot']
    sink=graph.node(r,c,'DI'+str(s));source=graph.node(r,c,'OF'+str(s));net=next(n for n in nets if sink in n['sinks'])
    assert net['source']==source and not net.get('clock')
    result=route(graph,[dict(net,sinks=[sink])],timeout=10,max_expansions=100)
    assert validate_routes(graph,[dict(net,sinks=[sink])],result)['connectivity_verified']
    assert result['nets'][net['name']]['pips']==[tuple(pair['pip'])]
    assert not graph.check_pip((r,c,'OF1','DI1'))


@pytest.mark.parametrize('mutation',['missing_role','wrong_data','second_ff','constant_clock','init','ce'])
def test_unmodeled_ff_relationships_are_rejected(candidate,mutation):
    _,_,original,placed,change=candidate;d=copy.deepcopy(original);p=copy.deepcopy(placed);row=p['clusters']['ng_coopt_404'];ff=d['modules']['top']['cells'][change['ff']]
    if mutation=='missing_role':del row['wide_ff_root']
    if mutation=='wrong_data':ff['connections']['D']=d['modules']['top']['cells'][row['lut']]['connections']['F'][:]
    if mutation=='second_ff':p['clusters']['ng_coopt_405']['ff']=change['ff']
    if mutation=='constant_clock':ff['connections']['CLK']=['1']
    if mutation=='init':ff['parameters']['INIT']='1'
    if mutation=='ce':ff['connections']['CE']=['0']
    with pytest.raises(ValueError):validate(d,p,Device())
