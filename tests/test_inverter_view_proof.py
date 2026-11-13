import copy,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_inverter_view_proof import compare_views


def fixture():
    a=dict(driver=11,inverted=False,instance_class='.?AVReg@NETLIST@@',pin_label='ff.CLEAR',native_inversion_annotation=True,
        inverter_chain_objects=[99],inverter_chain_physical=[False],inverter_input_annotations=[False])
    b=dict(a,inverted=True,inverter_chain_objects=[],inverter_chain_physical=[],inverter_input_annotations=[])
    return dict(modes={'ff':'unchanged'},merged_inverter_addresses=[99],sinks={'pin':a}),dict(modes={'ff':'unchanged'},merged_inverter_addresses=[99],sinks={'pin':b})


def test_recorded_control_not_is_counted_once_across_native_views():
    a,b=fixture();r=compare_views(a,b)
    assert not r['errors'] and r['control_inverter_proofs'][0]['truth_table']==[1,0]


def test_annotations_cannot_hide_unrecorded_or_real_routed_logic():
    for field,value in [('merged_inverter_addresses',[]),('physical',[True]),('input_inverted',[True])]:
        a,b=fixture()
        if field=='merged_inverter_addresses':a[field]=value
        else:a['sinks']['pin']['inverter_chain_physical' if field=='physical' else 'inverter_input_annotations']=value
        assert compare_views(a,b)['errors']


def test_changed_driver_clock_data_or_primitive_mode_is_not_a_control_fold():
    for kind in ('driver','clock','data','mode'):
        a,b=fixture()
        if kind=='driver':b['sinks']['pin']['driver']=22
        elif kind in ('clock','data'):
            for state in (a,b):state['sinks']['pin']['pin_label']='ff.'+('CLK' if kind=='clock' else 'D')
        else:b['modes']['ff']='changed'
        assert compare_views(a,b)['errors']


def test_missing_root_cannot_prove_an_inverter_identity():
    a,b=fixture()
    a['sinks']['pin']['driver']=b['sinks']['pin']['driver']=None
    assert compare_views(a,b)['errors']
