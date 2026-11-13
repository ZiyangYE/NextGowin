"""Real SAT counterexamples for an INIT or asynchronous-control mismatch."""
import sys,tempfile
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_official_source_equivalence import formal_library,proof_commands
from yosys_runtime import ROOT,run


@pytest.mark.parametrize('mutation',['none','init','reset'])
@pytest.mark.parametrize('separator',['.','/'])
def test_sequential_proof_rejects_wrong_initial_state_or_async_polarity(mutation,separator):
    with tempfile.TemporaryDirectory(dir=ROOT/'results') as temp:
        out=Path(temp);models,_=formal_library();(out/'models.v').write_text(models)
        gold="module top(input clk,d,rst,output reg q=0); always @(posedge clk or posedge rst) if(rst) q<=0; else q<=d; endmodule"
        init=int(mutation=='init');control='~rst' if mutation=='reset' else 'rst'
        gate=f"module top(input clk,d,rst,output q); DFFCE #(.INIT(1'b{init})) ff(.CLK(clk),.D(d),.CE(1'b1),.CLEAR({control}),.Q(q)); endmodule"
        (out/'gold.v').write_text(gold);(out/'gate.v').write_text(gate)
        commands=proof_commands(out/'gold.v',out/'gate.v',out/'models.v','top',out,separator)
        if mutation=='none':run(commands,out,'proof',30)
        else:
            with pytest.raises(RuntimeError):run(commands,out,'proof',30)
            assert 'proof did fail' in (out/'proof.log').read_text().lower()
