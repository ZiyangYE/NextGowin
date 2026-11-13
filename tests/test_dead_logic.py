import sys,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_dead_logic import prune


def test_kept_signal_and_state_inputs_survive_unobserved_logic_cleanup():
    def lut(i,o):return dict(type='LUT1',connections={'I0':[i],'F':[o]},port_directions={'I0':'input','F':'output'})
    cells={'a':lut(1,2),'dead':lut(1,3),'kept':lut(1,4),'zero_flag':lut(1,6),
        'ff':dict(type='DFFRE',connections={'D':[2],'Q':[5]},port_directions={'D':'input','Q':'output'})}
    d={'modules':{'top':dict(cells=cells,ports={'i':dict(direction='input',bits=[1])},netnames={'debug':dict(bits=[4],attributes={'keep':'1'}),'unused':dict(bits=[6],attributes={'keep':'0000'})})}}
    p=dict(clusters={n:dict(lut=n,ff=None,row=0,column=0,slot=i) for i,n in enumerate(['a','dead','kept','zero_flag'])},fixed_io={})
    original=copy.deepcopy(d);new,placed,record=prune(d,p)
    assert d==original and record['removed_cells']==['dead','zero_flag']
    assert new['modules']['top']['cells']['ff']==cells['ff'] and 'kept' in placed['clusters']


def test_live_wide_macro_is_kept_as_a_complete_physical_group():
    from ownpr_image_storage import load_json
    root=Path(__file__).resolve().parents[1];base=root/'results/vexriscv_84_6_joint_cut_place_002'
    d=load_json(base/'packed.json');p=load_json(base/'placement.json');new,placed,record=prune(d,p)
    assert 'ng_coopt_2918' in record['removed_cells']
    assert placed['fixed_io']==p['fixed_io'] and placed['ioreg_clusters']==p['ioreg_clusters']
    from ownpr_mux_macros import validate
    from ownpr_device import Device
    validate(new,placed,Device())
