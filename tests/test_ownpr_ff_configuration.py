import copy,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_ff_configuration import audit,compile_initial,independent_ff_groups


class NativeFfConfigurationTests(unittest.TestCase):
    def test_native_single_and_independent_groups(self):
        result=audit(ROOT/'results/native_ff_attributes_multi_001')
        self.assertEqual(result['status'],'verified_single_ff_controls')
        self.assertEqual(result['cases'],1024)
        self.assertEqual(result['independent_group_cases'],3072)
        self.assertEqual(result['independent_group_mismatch_indices'],[])

    def test_native_paired_connection_and_wrong_slot(self):
        result=audit(ROOT/'results/native_ff_attributes_paired_001')
        self.assertEqual(result['status'],'verified_single_ff_controls')
        self.assertTrue(result['paired_lut_data_verified'])
        self.assertFalse(result['full_bitstream_verified'])

    def test_initial_design_and_unverified_init(self):
        p=ROOT/'results/ownpr_int2float_place_003'
        design=json.loads((p/'packed.json').read_text());placement=json.loads((p/'placement.json').read_text())
        result=compile_initial(design,placement)
        self.assertEqual(len(result['paired_data_connections']),18)
        self.assertEqual(len(result['static_control_pins']),35)
        bad=copy.deepcopy(design);name=result['paired_data_connections'][0]['ff']
        bad['modules']['top']['cells'][name]['parameters']['INIT']='1'
        with self.assertRaisesRegex(ValueError,'Nondefault'):compile_initial(bad,placement)
        bad=copy.deepcopy(design);bad['modules']['top']['cells'][name]['connections']['D']=[-100]
        with self.assertRaisesRegex(ValueError,'paired LUT'):compile_initial(bad,placement)

    def test_shared_control_not_silently_accepted(self):
        ff=dict(cell_type='DFFRE',slot=0,ce='one',control='zero',clock='signal')
        with self.assertRaisesRegex(ValueError,'shared'):independent_ff_groups([ff,ff])
        ff['slot']=6
        with self.assertRaisesRegex(ValueError,'shared'):independent_ff_groups([dict(ff,slot=0),ff])

    def test_single_ff_extended_slots_match_independent_native_capture(self):
        from ownpr_ff_configuration import single_ff_any_slot_calibration
        from ownpr_image_storage import load_json
        folder=Path(__file__).resolve().parents[1]/'results/native_ff_attributes_paired_001'
        rows=load_json(folder/'ff_attributes.json')['records']
        self.assertEqual(len(single_ff_any_slot_calibration()),64)
        for slot in (1,3,5,6,7):
            row=next(r for r in rows if r['slot']==slot and r['cell_type']=='DFFRE' and r['ce']=='one' and r['control']=='signal' and r['clock']=='signal')
            ff=dict(cell_type='DFFRE',slot=slot,ce='one',control='signal',clock='signal')
            self.assertEqual(independent_ff_groups([ff]),row['attributes'])


if __name__=='__main__':unittest.main()
