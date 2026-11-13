import copy,json,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from lower_short_carry import lower
from native_boundary_cec import prove
from registered_rom_mapper import build,emit
from gowin_blif import read_blif
from restore_verific_memories import memory_attributes

class ResourceCandidateTests(unittest.TestCase):
    def test_all_alu_modes_cec_and_corruption_rejection(self):
        m=dict(attributes={},cells={},netnames={},ports={'inputs':dict(direction='input',bits=[2,3,4,5])})
        for mode in range(9):
            pins=dict(I0=[2],I1=[3],I3=[4],CIN=[5],SUM=[10+2*mode],COUT=[11+2*mode])
            m['cells']['alu'+str(mode)]=dict(type='ALU',parameters={'ALU_MODE':format(mode,'032b')},attributes={},connections=pins,port_directions={p:'output' if p in ('SUM','COUT') else 'input' for p in pins})
            m['ports']['out'+str(mode)]=dict(direction='output',bits=pins['SUM']+pins['COUT'])
        d=dict(modules={'top':m});after,r=lower(d);self.assertEqual(r['alu_cells_removed'],9)
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as tmp:
            prove(d,after,Path(tmp)/'good');bad=copy.deepcopy(after)
            bad['modules']['top']['cells']['alu0_ng_short_sum']['connections']['B']=['0']
            with self.assertRaises(ValueError):prove(d,bad,Path(tmp)/'bad')
        kept=copy.deepcopy(d);kept['modules']['top']['cells']['alu0']['attributes']['keep']='1'
        _,r=lower(kept);self.assertEqual(r['alu_cells_removed'],8)

    def test_rom_sdk_all_register_placements_and_init_guard(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as tmp:
            p=Path(tmp);source=p/'core.blif';source.write_text('.model core\n.inputs a b c\n.outputs x y\n.names a b x\n10 1\n01 1\n.names b c y\n11 1\n.end\n')
            for registers in ('input','output','internal'):
                _,r=build(source,p/registers,registers);self.assertEqual(r['status'],'verified');self.assertEqual(r['latency_cycles'],2)
            source.write_text('.model core\n.inputs a\n.outputs x\n.names a x\n0 1\n.end\n')
            with self.assertRaises(ValueError):emit(read_blif(source),'output')

    def test_memory_intent_is_explicit_and_conflicts_rejected(self):
        self.assertEqual(memory_attributes('wire [31:0] m;'),{})
        self.assertEqual(memory_attributes('/* verific ram_style="distributed", no_rw_check=0 */'),dict(ram_style='distributed',no_rw_check=0))
        with self.assertRaises(ValueError):memory_attributes('/* ram_style="block", syn_ramstyle="registers" */')
        with self.assertRaises(ValueError):memory_attributes('/* no_rw_check=maybe */')

if __name__=='__main__':unittest.main()
