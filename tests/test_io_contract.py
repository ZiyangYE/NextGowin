import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_io_contract import audit


def fixture():
    m=dict(ports=dict(clk=dict(direction='input',bits=[2]),a=dict(direction='input',bits=[3]),
        x=dict(direction='output',bits=['0']),y=dict(direction='output',bits=['0'])))
    c='IO_LOC "clk" A1; IO_LOC "a" A2; IO_LOC "x" A3; IO_LOC "y" A4;'
    s='create_clock -name clk -period 10 [get_ports {clk}]\n'
    for direction,names in [('input','a'),('output','x y')]:
        for mode in ('min','max'):s+=f'set_{direction}_delay -{mode} 0 -clock clk [get_ports {{{names}}}]\n'
    return m,c,s


def test_constant_alias_ports_remain_independent_contract_obligations():
    m,c,s=fixture();assert audit(m,c,s)['output_bits']==2
    with pytest.raises(ValueError,match='Missing min I/O bound for y'):audit(m,c,s.replace('set_output_delay -min 0 -clock clk [get_ports {x y}]','set_output_delay -min 0 -clock clk [get_ports {x}]'))


def test_wrong_direction_or_shared_package_pin_is_rejected():
    m,c,s=fixture()
    with pytest.raises(ValueError):audit(m,c.replace('A4','A3'),s)
    with pytest.raises(ValueError):audit(m,c,s.replace('get_ports {a}','get_ports {clk}'))
