from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from physical_replication import replicate,verify_copies
from gowin_cones import parse,readback
from wire_feedback import fit,predict
from run_abc_reproduction import cec


SOURCE='''module core(a,b,q,z);input a,b;output q,z;wire f;
LUT2 source(.I0(a),.I1(b),.F(f));defparam source.INIT=4'h6;
LUT1 target(.I0(f),.F(q));defparam target.INIT=2'h1;
LUT1 other(.I0(f),.F(z));defparam other.INIT=2'h2;
endmodule'''
REPORT='''3.3.1 Setup Analysis Report
Path1
Slack : -0.500
Data Arrival Path:
 0.1 0.1 tC2Q RR 1 R1C1[0][A] core/launch/Q
 0.2 0.1 tNET RR 1 R1C1[0][A] core/source/I0
 0.5 0.3 tINS RR 2 R1C1[0][A] core/source/F
 6.5 6.0 tNET RR 1 R1C200[0][A] core/target/I3
 6.8 0.3 tINS RR 1 R1C200[0][A] core/target/F
Data Required Path:
3.3.2 Hold Analysis Report
'''
MODEL=dict(coefficients=[.2,.02,.03,.1],floor_ns=.05)


class PhysicalReplicationTest(unittest.TestCase):
    def test_copy_uses_connectivity_instead_of_reported_physical_pin(self):
        candidate,old,new,result=replicate(SOURCE,'core',REPORT,MODEL,'core',max_copies=1,area_allowance=1)
        self.assertEqual(result['copied_luts'],1)
        self.assertEqual(result['actions'][0]['reported_sink_pin'],'I3')
        self.assertEqual(result['actions'][0]['logical_input_index'],0)
        self.assertLess(candidate.index('wire ngphys_net0;'),candidate.index('LUT1 target'))
        self.assertTrue(verify_copies(old,new,result['actions']))
        bad=candidate.replace("ngphys_copy0.INIT=4'h6","ngphys_copy0.INIT=4'h7")
        with self.assertRaises(ValueError):verify_copies(old,parse(bad,'core'),result['actions'])
        binary=ROOT/'build/abc/_TEST/abc.exe'
        if binary.exists():
            with tempfile.TemporaryDirectory() as temp:
                folder=Path(temp);a=folder/'source.blif';b=folder/'readback.blif'
                a.write_text(old['blif']);b.write_text(readback(new,old))
                self.assertTrue(cec(binary,a,b,folder/'cec.log',30)['equivalent'])

    def test_noncritical_wires_and_no_area_budget_keep_seed(self):
        for report,budget in [(REPORT.replace('6.0 tNET','.1 tNET'),1),(REPORT,0)]:
            with self.assertRaises(ValueError):replicate(SOURCE,'core',report,MODEL,'core',max_copies=1,area_allowance=budget)

    def test_nonnegative_wire_fit_recovers_known_coefficients(self):
        rows=[]
        for row,col,fanout in [(0,0,1),(10,0,1),(0,10,1),(0,0,8),(10,10,8)]:
            sample=dict(source_position=[0,0],sink_position=[row,col],fanout=fanout)
            expected=dict(coefficients=[.2,.03,.04,.2],floor_ns=.05)
            sample['delay_ns']=predict(expected,sample);rows.append(sample)
        model=fit(rows)
        for actual,wanted in zip(model['coefficients'],[.2,.03,.04,.2]):self.assertAlmostEqual(actual,wanted,places=8)


if __name__=='__main__':unittest.main()
