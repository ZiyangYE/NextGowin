import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_timing_pins import pins

def test_bus_bits_keep_independent_timing_names_and_scalar_clocks():
    cell=dict(connections={'ADA':[11,12,13],'DOA':[21,22],'CLKA':[31]},port_directions={'ADA':'input','DOA':'output','CLKA':'input'})
    assert list(pins(cell,'input'))==[('ADA[0]',11),('ADA[1]',12),('ADA[2]',13),('CLKA',31)]
    assert list(pins(cell,'output'))==[('DOA[0]',21),('DOA[1]',22)]
