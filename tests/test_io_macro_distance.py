import sys,math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_multilevel_coopt import physical_cost


def test_io_macro_guard_prevents_late_input_attraction_from_lengthening_output():
    d={'modules':{'top':{'cells':{
        's':dict(type='LUT1',connections={'I0':['0'],'F':[1]},port_directions={'I0':'input','F':'output'}),
        'r':dict(type='LUT1',connections={'I0':[1],'F':[10]},port_directions={'I0':'input','F':'output'}),
        'pad':dict(type='OBUF',connections={'I':[10],'O':[11]},port_directions={'I':'input','O':'output'})}}}}
    p=dict(clusters={'s':dict(row=0,column=0),'r':dict(row=9,column=8)},fixed_io={'pad':dict(native_ports={'out':[12,10,1]})})
    cut=dict(root='r',cut_bits=[1]);unbounded=physical_cost(d,p,cut,1);guarded=physical_cost(d,p,cut,1,0.)
    assert unbounded(5,5)<unbounded(9,8)
    assert math.isinf(guarded(5,5)) and math.isfinite(guarded(9,8)) and math.isfinite(guarded(10,9))
