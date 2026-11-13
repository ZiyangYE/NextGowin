import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_feedback_transport import compatible_pin,transport,protected_placement_cells
from ownpr_incremental_routes import signature


def design():
    return {'modules':{'top':{'cells':{n:dict(type='LUT1',parameters={'INIT':'10'},port_directions={'I0':'input','F':'output'},connections={'I0':[i],'F':[i+10]}) for i,n in enumerate(('a','b','c'),1)}}}}


def test_changed_function_and_new_cells_get_high_priority_without_changing_contract():
    old=design();new=copy.deepcopy(old);new['modules']['top']['cells']['b']['parameters']['INIT']='01'
    nets=[dict(name='n',source=1,sinks=[2,3,4,5],criticality=.5)]
    receivers=[dict(net='n',node=node,instance=name,pin='I0') for node,name in ((2,'a'),(3,'b'),(4,'c'),(5,'new'))]
    budgets={(n,'I0'):dict(original_setup_slack_ns=s) for n,s in [('a',0.),('b',4.),('c',2.),('unused_clock',-100.)]}
    out,policy=transport(nets,receivers,old,new,budgets)
    assert out[0]['sink_criticalities']=={2:1.,3:1.,4:.25,5:1.}
    assert signature(out[0])==signature(nets[0])
    assert 'sink_criticalities' not in nets[0]
    assert policy['known_receivers']==2 and policy['unknown_receivers']==2
    assert not policy['current_slacks_validated']


def test_shared_physical_pin_and_changed_connection_are_conservative():
    old=design();new=copy.deepcopy(old);new['modules']['top']['cells']['b']['connections']['I0']=[99]
    receivers=[dict(net='n',node=node,instance=name,pin='I0') for node,name in ((2,'a'),(3,'b'),(3,'c'))]
    nets=[dict(name='n',source=1,sinks=[2,3],criticality=.5),dict(name='clk',source=8,sinks=[9],criticality=1.,clock=True)]
    budgets={(n,'I0'):dict(original_setup_slack_ns=s) for n,s in [('a',0.),('b',4.),('c',2.)]}
    out,policy=transport(nets,receivers,old,new,budgets)
    assert not compatible_pin(old['modules']['top']['cells'],new['modules']['top']['cells'],'b','I0')
    assert out[0]['sink_criticalities'][3]==1. and policy['shared_receivers']==1
    assert out[1]==nets[1]


def test_no_supported_old_receiver_is_not_silently_accepted():
    with pytest.raises(ValueError,match='Empty'):
        transport([dict(name='n',source=0,sinks=[1])],[],design(),design(),{})


def test_incidental_moves_protect_setup_and_hold_without_using_clock_pin_slack():
    d=design();layout={'clusters':{n:{'ff':None} for n in ('a','b','c')}}
    budgets={(n,p):dict(original_setup_slack_ns=s,original_hold_slack_ns=h) for n,p,s,h in [('a','I0',.2,3.),('b','F',3.,.1),('c','I0',3.,4.),('c','CLK',-10.,-1.)]}
    protected,policy=protected_placement_cells(d,layout,budgets)
    assert protected=={'a','b'}
    assert len(policy['protected'])==2
    with pytest.raises(ValueError,match='window'):protected_placement_cells(d,layout,budgets,float('nan'))
