import copy,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_device import Device
from ownpr_place import constraints
from ownpr_validate import placement

@unittest.skipUnless((ROOT/'results/ownpr_int2float_place_002/placement.json').exists(),'Requires retained native-only placement fixture')
class NativePlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=ROOT/'results/ownpr_int2float_place_002';runtime=ROOT/'results/native_route_runtime_007'
        cls.design=json.loads((folder/'packed.json').read_text());cls.placed=json.loads((folder/'placement.json').read_text())
        cls.grid=json.loads((runtime/'native_grid_types.json').read_text())['grid'];cls.io=json.loads((runtime/'native_iob_ports.json').read_text());cls.device=Device()
        cls.contract=constraints(cls.design['modules']['top'],(ROOT/'results/int2float_clock_ablation_001/lut4.cst').read_text(),(ROOT/'results/fullio_closure_003/int_lut4_local155_edge.sdc').read_text())
    def check(self,p):return placement(self.design,p,self.grid,self.io,self.device.pin_site_name,self.contract)
    def test_real_native_placement_and_pin_corruption(self):
        self.assertTrue(self.check(self.placed)['fixed_package_pins_verified'])
        p=copy.deepcopy(self.placed);first=next(iter(p['fixed_io'].values()));first['package_pin']='A1'
        with self.assertRaisesRegex(ValueError,'package pin'):self.check(p)
    def test_collision_and_ff_pair_corruption(self):
        p=copy.deepcopy(self.placed);a,b=list(p['clusters'].values())[:2]
        for k in ('row','column','slot'):b[k]=a[k]
        with self.assertRaisesRegex(ValueError,'collision'):self.check(p)
        p=copy.deepcopy(self.placed);g=next(g for g in p['clusters'].values() if g['ff']);g['slot']=7
        with self.assertRaisesRegex(ValueError,'control group|collision'):self.check(p)

    def test_clock_policy_is_preserved_and_unknown_directives_rejected(self):
        cst=(ROOT/'results/int2float_clock_ablation_001/lut4.cst').read_text()
        sdc=(ROOT/'results/fullio_closure_003/int_lut4_local155_edge.sdc').read_text();module=self.design['modules']['top']
        contract=constraints(module,cst,sdc)
        self.assertEqual(contract['clock_routing'],[dict(net='ng_internal_clk',bit=22,policy='LOCAL_CLOCK')])
        with self.assertRaisesRegex(ValueError,'Unsupported explicit clock'):constraints(module,cst.replace('LOCAL_CLOCK','GLOBAL_CLOCK'),sdc)
        with self.assertRaisesRegex(ValueError,'routing target'):constraints(module,cst.replace('ng_internal_clk','missing_clock_net'),sdc)
        with self.assertRaisesRegex(ValueError,'Malformed clock'):constraints(module,cst.replace('LOCAL_CLOCK;','LOCAL_CLOCK'),sdc)
if __name__=='__main__':unittest.main()
