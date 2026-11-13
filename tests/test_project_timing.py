from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from project_timing import parse,implementation
from path_accounting import account
from compare_project_timing import compare
import copy


class ProjectTimingTest(unittest.TestCase):
    def setUp(self):
        directory=ROOT/'results/gowin_probe_005'
        if not (directory/'probe.tr').exists():self.skipTest('Native probe report unavailable')
        self.text=(directory/'probe.tr').read_text()
        self.sdc=(directory/'probe.sdc').read_text()
        self.profile=dict(part='GW5AST-LV138FPG676AC1/I0',clock_periods_ns=dict(probe_clk=10))

    def test_native_report_and_inactive_constraint_rejection(self):
        result=parse(self.text,self.sdc,self.profile)
        self.assertTrue(result['timing_closed'])
        self.assertEqual(result['frequency_reports']['probe_clk']['reported_fmax_mhz'],116.989)
        with self.assertRaises(ValueError):parse(self.text.replace('Actived','Ignored',1),self.sdc,self.profile)

    def test_path_failure_not_hidden_by_zero_tns(self):
        text=self.text.replace('1             1.452        capture2/Q','1             -0.100       capture2/Q')
        self.assertNotEqual(text,self.text)
        result=parse(text,self.sdc,self.profile)
        self.assertFalse(result['timing_closed'])
        self.assertTrue(any(r['kind']=='reported_path' for r in result['violations']))

    def test_additional_clock_requires_both_setup_and_hold_totals(self):
        marker='2.3 Max Frequency Summary\n  NO.'
        text=self.text.replace(marker,'  2 extra_clk Base 20.000 50.000MHz 0.000 10.000 extra\n\n'+marker)
        text=text.replace('3. Timing Details\n3.1','  extra_clk setup 0.000 0\n  extra_clk hold 0.000 0\n\n3. Timing Details\n3.1')
        sdc=self.sdc+'create_clock -name extra_clk -period 20 [get_ports {extra}]\n'
        text+='  TC_CLOCK Actived create_clock -name extra_clk -period 20 [get_ports {extra}]\n'
        profile=dict(self.profile,clock_periods_ns=dict(probe_clk=10,extra_clk=20))
        self.assertTrue(parse(text,sdc,profile)['timing_closed'])
        with self.assertRaises(ValueError):parse(text.replace('  extra_clk hold 0.000 0',''),sdc,profile)

    def test_physical_memory_budget_and_resource_types(self):
        text='''--LUT,ALU,ROM16 | 44410(39035 LUT, 5375 ALU, 0 ROM16) | -
Register | 48931/139140 | 36%
BSRAM | 10 SDPB 260 SDPX9B | 80%
'''
        parsed=implementation(text,dict(max_bsram=270))
        self.assertEqual(parsed['lut'],39035)
        self.assertEqual(parsed['registers'],48931)
        self.assertEqual(parsed['bsram'],270)
        with self.assertRaises(ValueError):implementation(text,dict(max_bsram=269))
        split=text.replace('BSRAM | 10 SDPB 260 SDPX9B | 80%','BSRAM | 270/340 | 80%\n--SDPB | 10\n--SDPX9B | 260')
        self.assertEqual(implementation(split,dict(max_bsram=270))['bsram'],270)
        with self.assertRaises(ValueError):implementation(split.replace('--SDPB | 10','--SDPB | 9'),dict(max_bsram=270))

    def test_full_project_path_accounting_excludes_launch_clock_tree(self):
        path=ROOT/'results/fpgafly_native_baseline_001/mapped.tr'
        if not path.exists():self.skipTest('Native full-project run unavailable')
        worst=account(path.read_text())[0]
        self.assertEqual(worst['path'],1)
        self.assertAlmostEqual(worst['route_delay_ns'],12.074,places=3)
        self.assertAlmostEqual(worst['data_delay_sum_ns'],13.693,places=3)

    def test_comparison_rejects_different_options_or_constraints(self):
        row=dict(part='p',dll_sha256='dll',native_tool_version='v',delay_models=dict(setup='s',hold='h'),
            clock_periods_ns=dict(clk=10),implementation_options=['-place_option 3'],
            artifacts_sha256={'project.sdc':'s','project.cst':'c'},timing_closed=True,
            violated_endpoints=dict(Setup=0,Hold=0),per_clock_tns=[],frequency_reports={},reported_paths=[],implementation={})
        self.assertTrue(compare(row,copy.deepcopy(row))['matched_clock_constraints_and_implementation_options'])
        changed=copy.deepcopy(row);changed['implementation_options']=['-place_option 2']
        with self.assertRaises(ValueError):compare(row,changed)
        changed=copy.deepcopy(row);changed['artifacts_sha256']['project.sdc']='other'
        with self.assertRaises(ValueError):compare(row,changed)


if __name__=='__main__':unittest.main()
