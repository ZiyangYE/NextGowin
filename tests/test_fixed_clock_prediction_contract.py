import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_fixed_clock_model_prediction import period_delta,clock_seed

SDC='create_clock -name clk -period 10 [get_ports {clk}]\nset_input_delay -max 0 -clock clk [get_ports {din[*]}]\nset_input_delay -min 0 -clock clk [get_ports {din[*]}]\n'


def test_only_period_change_is_accepted():
    assert period_delta(SDC,SDC.replace('-period 10','-period 6'))==-4


@pytest.mark.parametrize('changed',[
    SDC.replace('-max 0','-max 1'),
    SDC.replace('-period 10','-period 0'),
    SDC.replace('-period 10','-period nan'),
    SDC+'set_false_path -from [get_ports {din[0]}]\n',
    SDC+'create_clock -name aux -period 10 [get_ports {aux}]\n',
    SDC.replace('-period 10','-period 10 -waveform {0 4}'),
    SDC+'create_generated_clock -name sub [get_pins {x/Q}]\n',
    SDC.replace('-period 10','-period 6; set_false_path -to x'),
])
def test_exception_io_edge_and_domain_changes_are_rejected(changed):
    with pytest.raises(ValueError):period_delta(SDC,changed)


def test_primary_clock_buffer_is_selected_from_port_connectivity():
    d={'modules':{'top':{'ports':{'clk':{'direction':'input','bits':[2]}},'cells':{
        'clock_pad':{'type':'IBUF','connections':{'I':[2],'O':[12]}},
        'data_pad':{'type':'IBUF','connections':{'I':[3],'O':[13]}}}}}}
    assert clock_seed(d,'clk')=='clock_pad/O'
    d['modules']['top']['cells']['ambiguous']=d['modules']['top']['cells']['clock_pad']
    with pytest.raises(ValueError):clock_seed(d,'clk')
