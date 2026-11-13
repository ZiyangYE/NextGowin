import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from test_general_wide_recompile import grounded
from ownpr_wide_trust_region import movable,movable_atoms,translate,translate_group,within_reference_region,WideTrustRegionFeedback


def fixture():
    d,l,static,_=grounded(6)
    f=object.__new__(WideTrustRegionFeedback);f.reference=d;f.layout=l;f.static=static
    class Device:grid=[[17]*10]
    f.device=Device()
    return d,l,static,f


def test_rigid_vacant_translation_preserves_function_and_static_links():
    d,l,static,f=fixture();spec,_=movable(d,l)['root'];candidate=translate(l,spec,'root',(0,3));ns=copy.deepcopy(static)
    for row in ns:row['column']=3
    assert f.admit(d,candidate,ns)['capacity_verified']
    assert l['wide_macros'][0]['column']==0


def test_joint_cofactor_and_move_requires_exact_logic_and_moved_links():
    from ownpr_wide_trust_region import translate_recompile
    from ownpr_general_wide_recompile import info
    from ownpr_existing_wide_remap import macro_truth
    d,l,static,f=fixture();spec,boundary,members=info(d,l,'root')
    support=macro_truth(d,'root',boundary,members)['cut_bits']
    nd,nl,change=translate_recompile(d,l,'root',(0,3),2,support)
    ns=copy.deepcopy(static)
    for row in ns:row['column']=3
    assert f.admit(nd,nl,ns)['capacity_verified'] and change['joint_mapping_and_move']
    bad=copy.deepcopy(nd);bad['modules']['top']['cells']['leaf0']['parameters']['INIT']='0'*16
    with pytest.raises(ValueError):f.admit(bad,nl,ns)
    with pytest.raises(ValueError):f.admit(nd,nl,static)


def test_region_is_anchored_to_original_not_last_committed_position():
    d,l,static,f=fixture();spec,_=movable(d,l)['root'];moved=translate(l,spec,'root',(0,3))
    assert moved['wide_macros'][0]['column']==3
    assert within_reference_region(f,'root',(0,6))
    assert not within_reference_region(f,'root',(0,7))


def test_explicit_larger_search_radius_keeps_native_geometry_checks():
    d,l,static,f=fixture();f.trust_radius=12;spec,_=movable(d,l)['root']
    candidate=translate(l,spec,'root',(0,7));newstatic=copy.deepcopy(static)
    for row in newstatic:row['column']=7
    assert f.admit(d,candidate,newstatic)['capacity_verified']
    assert not within_reference_region(f,'root',(0,13))
    f.device.grid[0][7]=0
    with pytest.raises(ValueError):f.admit(d,candidate,newstatic)


def test_two_macro_atomic_translation_and_negative_controls():
    d,l,static,f=fixture();other=copy.deepcopy(d);ol=copy.deepcopy(l);os=copy.deepcopy(static)
    rename={n:'other_'+n for n in other['modules']['top']['cells']}
    def bits(xs):return [x+1000 if isinstance(x,int) else x for x in xs]
    for c in other['modules']['top']['cells'].values():
        for p,bs in c['connections'].items():c['connections'][p]=bits(bs)
    d['modules']['top']['cells'].update({rename[n]:c for n,c in other['modules']['top']['cells'].items()})
    for name,p in other['modules']['top']['ports'].items():
        d['modules']['top']['ports']['other_'+name]=dict(p,bits=bits(p['bits']))
    for name,row in ol['clusters'].items():l['clusters'][rename[name]]=dict(row,lut=rename[name],column=4)
    for name,row in ol['mux_clusters'].items():l['mux_clusters'][rename[name]]=dict(row,column=4)
    for m in ol['wide_macros']:l['wide_macros'].append(dict(m,root=rename[m['root']],column=4))
    for row in os:
        row.update(source=rename[row['source']],destination=rename[row['destination']],column=4)
    static.extend(os)
    # The fixture's reference objects intentionally include both macros.
    allowed=movable(d,l);targets={'root':(0,2),'other_root':(0,6)}
    candidate=translate_group(l,allowed,targets);newstatic=copy.deepcopy(static)
    for row in newstatic:row['column']=6 if row['destination'].startswith('other_') else 2
    assert f.admit(d,candidate,newstatic)['capacity_verified']
    assert l['wide_macros'][0]['column']==0 and l['wide_macros'][1]['column']==4
    with pytest.raises(ValueError):f.admit(d,candidate,static)
    with pytest.raises(ValueError):translate_group(l,allowed,{'root':(0,2),'other_root':(0,2)})


def test_private_ordinary_lut_and_wide_macro_can_swap_atomically():
    d,l,static,f=fixture();m=d['modules']['top'];output=m['cells']['root']['connections']['O'][0]
    m['cells']['glue']=dict(type='LUT1',parameters={'INIT':'10'},attributes={},port_directions={'I0':'input','F':'output'},connections={'I0':[output],'F':[901]})
    m['ports']['glue_out']=dict(direction='output',bits=[901]);l['clusters']['glue']=dict(lut='glue',ff=None,row=0,column=4,slot=3)
    atoms=movable_atoms(d,l,True);assert 'glue' in atoms
    candidate=translate_group(l,atoms,{'root':(0,4),'glue':(0,0)});newstatic=copy.deepcopy(static)
    for row in newstatic:row['column']=4
    with pytest.raises(ValueError):f.admit(d,candidate,newstatic)
    f.ordinary_group_moves=True
    assert f.admit(d,candidate,newstatic)['capacity_verified']
    changed=copy.deepcopy(d);changed['modules']['top']['cells']['glue']['parameters']['INIT']='01'
    with pytest.raises(ValueError):f.admit(changed,candidate,newstatic)
    l['clusters']['glue']['ff']='state'
    assert 'glue' not in movable_atoms(d,l,True)


@pytest.mark.parametrize('bad',['nonrigid','far','slot','links','occupied','ff'])
def test_state_overlap_geometry_and_static_mutations_are_rejected(bad):
    d,l,static,f=fixture();spec,_=movable(d,l)['root'];candidate=translate(l,spec,'root',(0,3));ns=copy.deepcopy(static)
    for row in ns:row['column']=3
    if bad=='nonrigid':candidate['clusters']['leaf0']['column']=2
    elif bad=='far':candidate=translate(l,spec,'root',(0,7))
    elif bad=='slot':candidate['clusters']['leaf0']['slot']=7
    elif bad=='links':ns[0]['column']=1
    elif bad=='occupied':l['arithmetic_clusters']={'fake':dict(row=0,column=3)};candidate['arithmetic_clusters']=copy.deepcopy(l['arithmetic_clusters'])
    else:l['clusters']['leaf0']['ff']='state'
    with pytest.raises(ValueError):f.admit(d,candidate,ns)
