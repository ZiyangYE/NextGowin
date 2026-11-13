import copy,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_multilevel_cut import Cuts,replace,wide_absorption_boundaries
from native_boundary_cec import prove

def sample():
    cells={}
    for name,ins,out,truth in [('a',[2,3],10,8),('b',[10,4,5],11,0x96),('root',[11,6,7],12,0xE8)]:
        cells[name]=dict(type='LUT'+str(len(ins)),parameters={'INIT':format(truth,f'0{1<<len(ins)}b')},attributes={},
            connections={**{f'I{i}':[b] for i,b in enumerate(ins)},'F':[out]},port_directions={**{f'I{i}':'input' for i in range(len(ins))},'F':'output'})
    return {'modules':{'top':dict(cells=cells,netnames={},ports={'in':dict(direction='input',bits=list(range(2,8))),'out':dict(direction='output',bits=[12]),'shared':dict(direction='output',bits=[10])})}}

class MultilevelCutTests(unittest.TestCase):
    def test_complete_lut6_and_downstream_gate_can_form_seven_input_macro(self):
        d=sample();cut=Cuts(d).function('root',frozenset(range(2,8)));d,_=replace(d,cut)
        m=d['modules']['top'];m['cells']['consumer']=dict(type='LUT2',parameters={'INIT':'1000'},attributes={},connections=dict(I0=[12],I1=[100],F=[101]),port_directions=dict(I0='input',I1='input',F='output'))
        m['ports']['in']['bits'].append(100);m['ports']['out']['bits']=[101];m['ports']['shared_wide']=dict(direction='output',bits=[12])
        model=Cuts(d,k=7);boundary=frozenset(list(range(2,8))+[100])
        self.assertIn(boundary,wide_absorption_boundaries(d,model,'consumer'))
        cut=model.function('consumer',boundary);new,change=replace(d,cut,late_bit=2)
        self.assertEqual(cut['cut_width'],7);self.assertEqual(change['lut_growth'],7)
        self.assertEqual(new['modules']['top']['cells']['consumer']['type'],'MUX2_LUT7')
        self.assertEqual(new['modules']['top']['cells']['consumer']['connections']['S0'],[2])
        from ownpr_mux_macros import tree
        spec=tree(new,'consumer');self.assertEqual(len(spec['leaves']),8);self.assertEqual(len(spec['muxes']),7)
        self.assertEqual(new['modules']['top']['cells']['root'],d['modules']['top']['cells']['root'])
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as t:
            self.assertTrue(prove(d,new,Path(t)/'valid')[0]['cec']['equivalent'])
            broken=copy.deepcopy(new);name=next(iter(spec['leaves']));word=broken['modules']['top']['cells'][name]['parameters']['INIT']
            broken['modules']['top']['cells'][name]['parameters']['INIT']=''.join('1' if c=='0' else '0' for c in word)
            with self.assertRaisesRegex(ValueError,'CEC failed'):prove(d,broken,Path(t)/'bad')
        with self.assertRaisesRegex(ValueError,'cut limits'):Cuts(d,k=8)

    def test_private_mux_data_link_cannot_become_routed_cut_boundary(self):
        d=sample();cs=d['modules']['top']['cells'];cs['a']=dict(type='LUT4',parameters={'INIT':'1000100010001000'},attributes={},connections={**{f'I{i}':[2+i] for i in range(4)},'F':[10]},port_directions={**{f'I{i}':'input' for i in range(4)},'F':'output'})
        cs['b']=copy.deepcopy(cs['a']);cs['b']['connections']['F']=[11];cs['b']['parameters']['INIT']='0111011101110111'
        cs['wide']=dict(type='MUX2_LUT5',parameters={},connections=dict(I0=[10],I1=[11],S0=[6],O=[13]),port_directions=dict(I0='input',I1='input',S0='input',O='output'))
        cs['root']=dict(type='LUT1',parameters={'INIT':'10'},connections=dict(I0=[13],F=[12]),port_directions=dict(I0='input',F='output'))
        del d['modules']['top']['ports']['shared']
        m=Cuts(d);cut=m.function('root',{10,11,6})
        with self.assertRaisesRegex(ValueError,'private dedicated'):replace(d,cut)
        cut=m.function('root',{2,3,4,5,6});new,_=replace(d,cut)
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as t:self.assertTrue(prove(d,new,Path(t)/'valid')[0]['cec']['equivalent'])

    def test_three_levels_become_lut6_and_shared_parent_is_preserved(self):
        d=sample();original=copy.deepcopy(d);model=Cuts(d);boundary=frozenset(range(2,8))
        self.assertIn(boundary,model.enumerate(12));cut=model.function('root',boundary)
        self.assertEqual(cut['interior_count'],3)
        new,change=replace(d,cut,late_bit=2);self.assertEqual(d,original)
        self.assertEqual(new['modules']['top']['cells']['root']['connections']['S0'],[2])
        self.assertEqual(new['modules']['top']['cells']['a'],d['modules']['top']['cells']['a'])
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as t:
            self.assertTrue(prove(d,new,Path(t)/'valid')[0]['cec']['equivalent'])
            broken=copy.deepcopy(new);leaf=change['added_cells'][0];broken['modules']['top']['cells'][leaf]['parameters']['INIT']='0000000000000000'
            with self.assertRaisesRegex(ValueError,'CEC failed'):prove(d,broken,Path(t)/'bad')

    def test_cut_with_functionally_eliminated_input_retains_evaluation_boundary(self):
        d=sample();d['modules']['top']['cells']['root']['parameters']['INIT']='10101010'
        m=Cuts(d);cut=m.function('root',frozenset(range(2,8)))
        self.assertEqual(len(cut['evaluation_boundary']),6);self.assertEqual(cut['cut_width'],4)
        new,change=replace(d,cut)
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as t:self.assertTrue(prove(d,new,Path(t)/'valid')[0]['cec']['equivalent'])
        self.assertEqual(change['lut_growth'],0)

    def test_invalid_certificate_boundary_and_cycle_rejected(self):
        d=sample();m=Cuts(d);cut=m.function('root',frozenset(range(2,8)));cut['truth_hex']='0x0'
        with self.assertRaisesRegex(ValueError,'certificate'):replace(d,cut)
        with self.assertRaisesRegex(ValueError,'cover'):m.function('root',{2,3})
        d['modules']['top']['cells']['a']['connections']['I0']=[12]
        with self.assertRaisesRegex(ValueError,'cycle'):Cuts(d).enumerate(12)
        d=sample();d['modules']['top']['cells']['root']['parameters']['UNMODELED']='1'
        with self.assertRaisesRegex(ValueError,'parameter contract'):Cuts(d)

if __name__=='__main__':unittest.main()
