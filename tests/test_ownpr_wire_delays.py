import copy,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from audit_native_wire_delays import audit,replay
from ownpr_cell_timing import NativeCellTiming


class NativeWireDelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=ROOT/'results/native_wire_delays_005'
        cls.data=json.loads((cls.folder/'native_wire_delays.json').read_text())

    def test_real_paths_match_queries_but_report_residuals_are_not_hidden(self):
        result=audit(self.folder)
        self.assertEqual(result['status'],'native_node_queries_replayed')
        self.assertEqual(result['paths'],324)
        self.assertEqual(result['query_mismatches'],[])
        comparison=result['report_comparison']
        self.assertEqual((comparison['rows'],comparison['matched_rows']),(164,133))
        self.assertEqual(comparison['unmapped_rows'],[])
        self.assertFalse(result['timing_signoff'])
        self.assertFalse(result['full_timing_model_verified'])

    def test_wrong_delay_is_detected(self):
        data=copy.deepcopy(self.data);row=next(r for r in data['nodes'] if any(r['values']))
        row['values']=[v+0.25 for v in row['values']]
        row['values_by_selector'][data['active_selector']]=row['values'][:]
        self.assertEqual(replay(data)['status'],'failed')

    def test_broken_parent_and_empty_path_are_rejected(self):
        data=copy.deepcopy(self.data);row=next(r for r in data['nodes'] if r['parent']);row['parent']=None
        with self.assertRaisesRegex(ValueError,'parent chain'):replay(data)
        data=copy.deepcopy(self.data);data['paths'][0]['path_sink_to_source']=[]
        with self.assertRaisesRegex(ValueError,'endpoint chain'):replay(data)

    def test_native_lut_buffer_modes_close_report_residuals(self):
        result=audit(ROOT/'results/native_wire_delays_009')
        self.assertEqual(result['local_adjusted_paths'],16)
        self.assertEqual(result['report_comparison']['matched_rows'],133)
        corrected=result['report_with_local_delays']
        self.assertEqual((corrected['rows'],corrected['matched_rows']),(164,164))
        self.assertTrue(all(not g['unexplained_residuals'] for g in corrected['groups']))
        self.assertFalse(result['timing_signoff'])

    def test_slot_group_and_absent_implicit_buffer_are_distinguished(self):
        library=NativeCellTiming()
        self.assertNotEqual(library.lut_input(3,5,1),library.lut_input(3,6,1))
        self.assertEqual(library.passthrough(27,1,True),library.lut_input(3,6,1))
        self.assertEqual(library.passthrough(27,1,False),(0.,0.,0.,0.))
        with self.assertRaises(ValueError):library.passthrough(295,1,True)
        with self.assertRaises(ValueError):library.lut_input(4,0,1)


if __name__=='__main__':unittest.main()
