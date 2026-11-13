import copy,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from formal_state_values import propose,add_assertion
from yosys_runtime import run,quote


def fixture(initial=None,data='0'):
    wire={'hide_name':0,'bits':[3],'attributes':{}}
    if initial is not None:wire['attributes']['init']=initial
    module={'attributes':{'top':'1'},'ports':{'d':{'direction':'input','bits':[2]},'q':{'direction':'output','bits':[3]}},
            'netnames':{'q':wire,'d':{'hide_name':0,'bits':[2],'attributes':{}}},
            'cells':{'state':{'type':'$ff','hide_name':0,'parameters':{'WIDTH':'1'},'attributes':{},'connections':{'D':[data],'Q':[3]},'port_directions':{'D':'input','Q':'output'}}}}
    return {'modules':{'product':module}}


def test_uninitialized_constant_successor_keeps_unknown_in_domain():
    d=fixture();before=copy.deepcopy(d);p=propose(d['modules']['product'])
    assert p['domains']=={3:5} and d==before
    monitored=add_assertion(d,p['domains'])
    assert monitored['modules']['product']['netnames']['q']['attributes']=={}
    assert monitored['modules']['product']['cells']['state']==d['modules']['product']['cells']['state']


def test_free_data_does_not_become_a_constant_state_invariant():
    assert propose(fixture('0',2)['modules']['product'])['domains']=={3:3}
    assert propose(fixture(None,2)['modules']['product'])['domains']=={}
    assert propose(fixture(None,'1')['modules']['product'])['domains']=={3:6}


def test_actual_yosys_proves_unknown_or_zero_without_zeroing_original_init(tmp_path):
    d=fixture();p=propose(d['modules']['product']);file=tmp_path/'model.json';file.write_text(json.dumps(add_assertion(d,p['domains'])))
    run(['read_json '+quote(file),'hierarchy -top product','check -assert',
         'sat -seq 1 -set-init-undef -set-def-inputs -prove-asserts -verify',
         'sat -seq 1 -tempinduct -maxsteps 1 -set-init-undef -set-def-inputs -prove-asserts -verify'],tmp_path,'valid',20)


def test_invented_zero_invariant_rejects_at_original_initial_state(tmp_path):
    file=tmp_path/'wrong.json';file.write_text(json.dumps(add_assertion(fixture(),{3:1})))
    with pytest.raises(RuntimeError,match='Yosys failed'):
        run(['read_json '+quote(file),'hierarchy -top product','check -assert','sat -seq 1 -set-init-undef -set-def-inputs -prove-asserts -verify'],tmp_path,'reject',20)


@pytest.mark.parametrize('domains',[{}, {99:1}, {3:0}, {3:7}])
def test_invalid_or_vacuous_domain_proposals_reject(domains):
    with pytest.raises(ValueError):add_assertion(fixture(),domains)
