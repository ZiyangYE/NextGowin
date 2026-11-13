"""Keep registered-DSP coverage explicit; reject unsupported modes and bits."""
import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_hard_architecture import HardArchitecture
from ownpr_hard_timing_model import NETLIST
from ownpr_image_storage import load_json
from ownpr_native_netlist import inactive_clock_projection
from ownpr_timing_endpoints import contract


def fixture():
    d=load_json(NETLIST);d['modules']['top']=d['modules'].pop('VexRiscv')
    for c in d['modules']['top']['cells'].values():
        if c['type']=='MULTALU27X18':c['parameters']['OREG_CLK']='CLK0'
    return d


def test_registered_clock_and_data_endpoints_are_not_bypassed():
    d=fixture();h=HardArchitecture();spec=h.contract(d)
    dsps=[n for n,c in d['modules']['top']['cells'].items() if c['type']=='MULTALU27X18']
    for n in dsps:
        assert spec[n]['clock_pins']==['CLK[0]']
        assert all(p in spec[n]['endpoints'] for p in ('CLK[0]','CE[0]','RESET[0]'))
    projections=inactive_clock_projection(d,spec)
    assert len(projections)==4 and all(p['index']==1 for p in projections)
    mapping={'instances':{n:n for n in d['modules']['top']['cells']}}
    endpoints=contract(d,mapping)
    assert all((n,'CLK[0]') in endpoints['clocks'] for n in dsps)
    assert not any((n,'CLK[1]') in endpoints['clocks'] for n in dsps)


def test_registered_matrix_never_uses_combinational_delay():
    h=HardArchitecture();cell=h.timing.registered_data['cell']
    assert len(h.timing.profiles[h.timing.registered_key]['edges'])==128
    assert h.timing.matrices(cell,'CLK[0]','DOUT[0]',10)[0][0]>0
    with pytest.raises(ValueError,match='No calibrated'):h.timing.matrices(cell,'A[0]','DOUT[0]',1)
    changed=copy.deepcopy(cell);changed['parameters']['PREG_CLK']='CLK0'
    with pytest.raises(ValueError,match='Uncalibrated'):h.timing.matrices(changed,'CLK[0]','DOUT[0]',10)


def test_unmeasured_output_bit_rejected_when_observed():
    d=fixture();cells=d['modules']['top']['cells'];dsp=next(c for c in cells.values() if c['type']=='MULTALU27X18')
    cells['unexpected_consumer']={'type':'OBUF','parameters':{},'connections':{'I':[dsp['connections']['DOUT'][40]],'O':[9999999]},'port_directions':{'I':'input','O':'output'}}
    with pytest.raises(ValueError,match='no independent C2Q'):HardArchitecture().contract(d)


def test_cascade_and_unmeasured_ce_selection_rejected():
    for change in ('cascade','ce'):
        d=fixture();cells=d['modules']['top']['cells'];dsp=next(c for c in cells.values() if c['type']=='MULTALU27X18')
        if change=='cascade':cells['consumer']={'type':'OBUF','parameters':{},'connections':{'I':[dsp['connections']['CASO'][0]],'O':[9999999]},'port_directions':{'I':'input','O':'output'}}
        else:dsp['parameters']['OREG_CE']='CE1'
        with pytest.raises(ValueError):HardArchitecture().contract(d)
