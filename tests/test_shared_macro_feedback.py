import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_shared_feedback import normalize_clone_inputs


def setup():
    def lut(bits,out):return dict(type='LUT4',parameters={'INIT':'0110100110010110'},attributes={},hide_name=1,connections={**{'I'+str(i):[b] for i,b in enumerate(bits)},'F':[out]},port_directions={**{'I'+str(i):'input' for i in range(4)},'F':'output'})
    d={'modules':{'top':{'cells':{'root':lut([1,2,3,4],10),'leaf':lut([10,2,3,4],20)},'ports':{},'netnames':{}}}}
    l={'clusters':{n:dict(lut=n,ff=None,row=i+1,column=1,slot=0) for i,n in enumerate(['root','leaf'])}}
    nd=copy.deepcopy(d);nl=copy.deepcopy(l);nd['modules']['top']['cells']['legacy']=lut([1,2,3,4],30);nd['modules']['top']['cells']['leaf']['connections']['I0']=[30]
    nl['clusters']['root']['row']=3;nl['clusters']['legacy']=dict(lut='legacy',ff=None,row=1,column=1,slot=0)
    return d,nd,l,nl


def test_exact_clone_wire_allowed_without_changing_actual_candidate_or_leaf_function():
    d,n,l,nl=setup();saved=copy.deepcopy(n)
    audit,changes=normalize_clone_inputs(d,n,l,nl,{'leaf'})
    assert audit['modules']['top']['cells']['leaf']==d['modules']['top']['cells']['leaf']
    assert n==saved and changes==[dict(leaf='leaf',pin='I0',source='root',clone='legacy')]
    assert audit['modules']['top']['cells']['legacy']==n['modules']['top']['cells']['legacy']


@pytest.mark.parametrize('change',['truth','output','move','clone_truth','clone_input','protected'])
def test_wide_leaf_and_clone_function_contracts_fail_closed(change):
    d,n,l,nl=setup();cells=n['modules']['top']['cells']
    if change=='truth':cells['leaf']['parameters']['INIT']='0'*16
    elif change=='output':cells['leaf']['connections']['F']=[21]
    elif change=='move':nl['clusters']['leaf']['row']=4
    elif change=='clone_truth':cells['legacy']['parameters']['INIT']='0'*16
    elif change=='clone_input':cells['legacy']['connections']['I1']=[5]
    else:d['modules']['top']['cells']['leaf']['attributes']['dont_touch']='1'
    with pytest.raises(ValueError):normalize_clone_inputs(d,n,l,nl,{'leaf'})


def mux_setup():
    d,n,l,nl=setup()
    cell=dict(type='MUX2_LUT5',parameters={},attributes={},connections={'I0':[40],'I1':[41],'S0':[10],'O':[42]},port_directions={'I0':'input','I1':'input','S0':'input','O':'output'})
    d['modules']['top']['cells']['mux']=copy.deepcopy(cell);n['modules']['top']['cells']['mux']=copy.deepcopy(cell)
    n['modules']['top']['cells']['mux']['connections']['S0']=[30]
    l['mux_clusters']={'mux':dict(row=5,column=5,slot=0,level=5)};nl['mux_clusters']=copy.deepcopy(l['mux_clusters'])
    return d,n,l,nl


def test_mux_external_selector_exact_copy_keeps_private_data_and_candidate():
    d,n,l,nl=mux_setup();saved=copy.deepcopy(n);audit,changes=normalize_clone_inputs(d,n,l,nl,{'leaf'})
    assert audit['modules']['top']['cells']['mux']==d['modules']['top']['cells']['mux'] and n==saved
    assert changes[-1]==dict(mux='mux',pin='S0',source='root',clone='legacy')


@pytest.mark.parametrize('change',['private_input','output','move','type','clone_truth','protected'])
def test_mux_copy_substitution_rejects_real_private_or_function_changes(change):
    d,n,l,nl=mux_setup();c=n['modules']['top']['cells']
    if change=='private_input':c['mux']['connections']['I0']=[30]
    elif change=='output':c['mux']['connections']['O']=[43]
    elif change=='move':nl['mux_clusters']['mux']['row']=6
    elif change=='type':c['mux']['type']='MUX2_LUT6'
    elif change=='clone_truth':c['legacy']['parameters']['INIT']='0'*16
    else:d['modules']['top']['cells']['mux']['attributes']['keep']='1'
    with pytest.raises(ValueError):normalize_clone_inputs(d,n,l,nl,{'leaf'})


def test_nested_exact_clone_dag_accepted_but_changed_parent_truth_rejected():
    d,n,l,nl=setup();old=d['modules']['top']['cells'];new=n['modules']['top']['cells']
    parent=copy.deepcopy(old['root']);parent['connections']['F']=[50]
    old['parent']=copy.deepcopy(parent);new['parent']=copy.deepcopy(parent)
    old['root']['connections']['I0']=[50];new['root']['connections']['I0']=[50];new['legacy']['connections']['I0']=[60]
    new['parent_copy']=copy.deepcopy(parent);new['parent_copy']['connections']['F']=[60]
    audit,changes=normalize_clone_inputs(d,n,l,nl,{'leaf'})
    assert changes[0]['clone']=='legacy'
    new['parent_copy']['parameters']['INIT']='0'*16
    with pytest.raises(ValueError,match='exact ordinary'):normalize_clone_inputs(d,n,l,nl,{'leaf'})
