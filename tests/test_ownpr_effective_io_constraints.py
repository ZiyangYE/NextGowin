import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_effective_io_constraints import audit


def test_current_report_accepts_and_memory_compensation_is_rejected():
    good=ROOT/'results/coopt_review_stage3_reexport_001/native'
    bad=ROOT/'reviews/coopt_200mhz_20261104/results/coopt_uart_stage4_001/export/native'
    a=audit((good/'probe.tr').read_text(),(good/'probe.sdc').read_text())
    assert a['rows']=={'tIn':191,'tOut':22}
    with pytest.raises(ValueError,match='Effective I/O delay differs'):
        audit((bad/'probe.tr').read_text(),(good/'probe.sdc').read_text())


@pytest.mark.parametrize('kind',['Setup','Hold','Recovery','Removal'])
def test_checks_every_timing_section(kind):
    sdc='set_input_delay -max 0 -clock clk [get_ports {a}]'
    text=f'3.3.1 {kind} Analysis Report\n 0.000 0.000 tIn IOB1 x\n 5.000 0.000 tOut IOB2 y\n'
    assert audit(text,sdc)['rows']['tIn']==1
    with pytest.raises(ValueError,match='Effective I/O delay differs'):audit(text.replace('0.000 tIn','0.010 tIn'),sdc)


def test_missing_io_rows_and_nonzero_contract_fail_closed():
    with pytest.raises(ValueError,match='Missing effective'):audit('','set_input_delay -max 0 -clock clk x')
    with pytest.raises(ValueError,match='requires zero'):audit('','set_input_delay -max 1 -clock clk x')
def test_native_grouped_io_sections_are_checked_not_skipped():
    from ownpr_effective_io_constraints import audit
    sdc='set_input_delay -min 0 -clock clk [get_ports a]\nset_output_delay -max 0 -clock clk [get_ports q]\n'
    text='3.3.1 Setup Analysis Report\n3.3.1.1 Setup Analysis Report[1]\n 0.000 0.000 tIn\n 50.000 0.000 tOut\n'
    assert audit(text,sdc)['rows']=={'tIn':1,'tOut':1}
    import pytest
    with pytest.raises(ValueError,match='differs'):audit(text.replace('0.000 tOut','0.125 tOut'),sdc)
    with pytest.raises(ValueError,match='outside'):audit(text.replace('3.3.1.1 Setup Analysis Report[1]','3.4.1 Unsupported Report'),sdc)

