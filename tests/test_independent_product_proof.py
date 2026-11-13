"""Exercise the actual independent-product initial and induction proof gates."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from yosys_runtime import run, quote


def product(tmp_path, gold_initial='x', gate_initial='0', bad_transition=False):
    source = tmp_path / 'pair.v'
    gate_d = '~d' if bad_transition else 'd'
    source.write_text(
        f"module gold(input clk,d,output reg q); initial q=1'b{gold_initial}; always @(posedge clk) q<=d; endmodule\n"
        f"module gate(input clk,d,output reg q); initial q=1'b{gate_initial}; always @(posedge clk) q<={gate_d}; endmodule\n"
    )
    run(['read_verilog -formal '+quote(source), 'proc', 'clk2fflogic',
         'miter -equiv -ignore_gold_x -make_assert -make_outputs -flatten gold gate product',
         'hierarchy -check -top product', 'opt', 'check -assert',
         'write_rtlil '+quote(tmp_path/'product.il')], tmp_path, 'build', 20)
    return ['read_rtlil '+quote(tmp_path/'product.il'), 'check -assert']


def test_original_unknown_initialization_refines_without_fanout_matching(tmp_path):
    commands = product(tmp_path)
    run(commands+['sat -seq 4 -set-init-undef -set-def-inputs -prove-asserts -verify'], tmp_path, 'base', 20)
    run(commands+['sat -seq 4 -tempinduct -maxsteps 4 -set-init-undef -set-def-inputs -prove-asserts -verify'], tmp_path, 'induction', 20)


@pytest.mark.parametrize('initial,transition', [('1',False), ('x',True)])
def test_actual_defined_initial_or_transition_mismatch_rejects(tmp_path, initial, transition):
    commands = product(tmp_path, gold_initial=initial, bad_transition=transition)
    with pytest.raises(RuntimeError, match='Yosys failed'):
        run(commands+['sat -seq 4 -set-init-undef -set-def-inputs -prove-asserts -verify'], tmp_path, 'reject', 20)


def test_direct_four_step_induction_actually_executes_the_requested_solve(tmp_path):
    commands=product(tmp_path)
    run(commands+['sat -seq 1 -tempinduct-inductonly -initsteps 3 -maxsteps 4 -set-init-undef -set-def-inputs -prove-asserts -verify'],tmp_path,'direct',20)
    log=(tmp_path/'direct.log').read_text()
    assert '[induction step 4] Solving problem' in log and 'Induction step proven: SUCCESS!' in log


def test_equal_initsteps_and_maxsteps_is_not_a_real_counterexample(tmp_path):
    commands=product(tmp_path)
    with pytest.raises(RuntimeError,match='Yosys failed'):
        run(commands+['sat -seq 1 -tempinduct-inductonly -initsteps 4 -maxsteps 4 -set-init-undef -set-def-inputs -prove-asserts -verify'],tmp_path,'skipped',20)
    log=(tmp_path/'skipped.log').read_text()
    assert '[induction step 4] Solving problem' not in log
