import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_hard_timing_model import HardCellTiming,audit


def test_full_cpu_hard_cells_match_independent_matrices():
    result=audit(ROOT/'results/native_vexriscv_hard_timing_002')
    assert result['edges_by_primitive']=={'DPX9B':840,'SDPX9B':616,'MULTALU27X18':6624}
    assert result['profile_count']==4 and len(result['instances'])==14
    assert not result['whole_design_timing_signoff'] and not result['hard_resource_routing_verified']


def test_unobserved_modes_and_undefined_arcs_never_become_zero_delay():
    model=HardCellTiming()
    memory=next(c for c in model.cells.values() if c['type']=='DPX9B' and int(c['parameters']['BIT_WIDTH_0'],2)==18)
    changed=copy.deepcopy(memory);changed['parameters']['READ_MODE0']='1'
    with pytest.raises(ValueError,match='Uncalibrated'):model.matrices(changed,'CLKA','DOA[0]',10)
    with pytest.raises(ValueError,match='Undefined'):model.matrices(memory,'CLKA','DOB[0]',10)
    with pytest.raises(ValueError,match='kind mismatch'):model.matrices(memory,'CLKA','DOA[0]',20)
    dsp=next(c for c in model.cells.values() if c['type']=='MULTALU27X18')
    with pytest.raises(ValueError,match='No calibrated'):model.matrices(dsp,'A[26]','DOUT[0]',1)
    original=model.matrices(dsp,'A[0]','DOUT[47]',1);altered=model.matrices(dsp,'A[0]','DOUT[47]',1);altered[0][0]=999
    assert model.matrices(dsp,'A[0]','DOUT[47]',1)==original
