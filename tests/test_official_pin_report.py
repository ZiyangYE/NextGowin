import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from official_pin_report import audit
from ownpr_image_storage import read_text,load_json
from ownpr_place import constraints


def test_public_html_fixed_io_and_wrong_pin_negative():
    src=ROOT/'results/fair_native_baseline_trial_021/official_cli_cohort_001/int2float'
    proof=ROOT/'results/fair_native_baseline_trial_021/official_cli_source_proofs/int2float'
    module=load_json(proof/'gold_hierarchy.json')['modules']['top']
    contract=constraints(module,read_text(src/'fixed.cst'),read_text(src/'clock.sdc'))
    text=read_text(src/'project/baseline/impl/pnr/baseline.pin.html')
    assert audit(text,contract)['ports']==19
    with pytest.raises(ValueError,match='Package pin changed'):
        audit(text.replace('<td>L22/3</td>','<td>BAD/3</td>',1),contract)
    with pytest.raises(ValueError,match='electrical'):
        audit(text.replace('<td>LVCMOS33</td>','<td>LVCMOS18</td>',1),contract)
    with pytest.raises(ValueError,match='header'):
        audit(text.replace('BankVccio','WrongColumn'),contract)
