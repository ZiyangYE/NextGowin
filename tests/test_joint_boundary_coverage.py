import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_joint_boundary_coverage import summarize


def test_inventory_does_not_hide_unchecked_controls_or_other_regions():
    cells={'ff':dict(type='DFFCE',port_directions={'D':'input','CE':'input','CLK':'input'}),
           'ram':dict(type='DPX9B',port_directions={'ADA':'input','CEA':'input','CLKA':'input'})}
    layout=dict(clusters={'lut':dict(row=1,column=2,ff='ff')},fixed_io={},hard_clusters={'ram':dict(row=40,column=40,family='bsram')})
    graph=dict(nodes=['ff/D','ram/ADA'],edges=[],seeds={'ff/D':[3.,3.],'ram/ADA':[1.,1.]})
    model=dict(late=graph,early=graph,setup_requirements={'ff/D':[2.,2.],'ram/ADA':[2.,2.]},hold_requirements={'ff/D':[0.,0.],'ram/ADA':[0.,0.]})
    report=summarize(model,dict(modules={'top':dict(cells=cells)}),layout)
    assert len(report['endpoints'])==4
    assert {tuple(r['region']) for r in report['groups']}=={(0,0),(2,2)}
    unchecked=[r for r in report['physical_input_inventory'] if r['endpoint']=='ff/CE']
    assert len(unchecked)==2 and all(r['classification']=='unresolved_constant_inactive_or_missing_check' for r in unchecked)
    assert sum(r['violations'] for r in report['groups'])==1
    assert report['timing_signoff'] is False
