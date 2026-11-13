import copy,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_register_equivalence import collapse,prove_replicas

def sample():
    lut=dict(type='LUT1',parameters={'INIT':'10'},port_directions={'I0':'input','F':'output'},connections={'I0':[2],'F':[5]})
    ff=dict(type='DFFRE',parameters={'INIT':'0'},port_directions={p:'output' if p=='Q' else 'input' for p in ('D','CLK','CE','RESET','Q')},connections=dict(D=[5],CLK=[3],CE=['1'],RESET=['0'],Q=[6]))
    before={'modules':{'top':dict(cells={'d':lut,'state':ff},netnames={},ports={'d':dict(direction='input',bits=[2]),'clk':dict(direction='input',bits=[3]),'q':dict(direction='output',bits=[6])})}}
    after=copy.deepcopy(before);m=after['modules']['top'];m['cells']['copy_d']=copy.deepcopy(lut);m['cells']['copy_d']['connections']['F']=[7]
    m['cells']['copy']=copy.deepcopy(ff);m['cells']['copy']['connections'].update(D=[7],Q=[8]);m['ports']['q']['bits']=[8]
    return before,after,{'copy':'state'}

class RegisterReplicaProofTests(unittest.TestCase):
    def test_identical_transition_and_unchanged_blackboxes(self):
        a,b,r=sample();b['modules']['top']['cells']['copy']['connections']['D']=[5]
        for d in (a,b):d['modules']['LIB']=dict(attributes={'blackbox':'1'},ports={})
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as tmp:
            p=prove_replicas(a,b,r,Path(tmp)/'valid');self.assertTrue(p['transition_structurally_identical'])
        b['modules']['LIB']['ports']['new']={}
        with self.assertRaisesRegex(ValueError,'unchanged blackboxes'):collapse(a,b,r)

    def test_replica_proved_and_original_boundary_checker_stays_strict(self):
        from native_boundary_cec import prove
        a,b,r=sample()
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as tmp:
            with self.assertRaisesRegex(ValueError,'instance set changed'):prove(a,b,Path(tmp)/'ordinary')
            p=prove_replicas(a,b,r,Path(tmp)/'inductive')
            self.assertEqual(p['status'],'register_replica_invariant_proved')

    def test_wrong_transition_rejected(self):
        for pin,value in [('D',['0']),('CE',['0']),('RESET',['1'])]:
            with self.subTest(pin=pin),tempfile.TemporaryDirectory(dir=ROOT/'results') as tmp:
                a,b,r=sample();b['modules']['top']['cells']['copy']['connections'][pin]=value
                with self.assertRaisesRegex(ValueError,'CEC failed'):prove_replicas(a,b,r,Path(tmp)/'bad')

    def test_initial_clock_extra_state_and_multiple_drivers_rejected(self):
        for mutation in ('init','clock','extra','driver','async'):
            with self.subTest(mutation=mutation):
                a,b,r=sample();cs=b['modules']['top']['cells']
                if mutation=='init':b['modules']['top']['netnames']['init']=dict(bits=[8],attributes={'init':'1'})
                if mutation=='clock':cs['copy']['connections']['CLK']=[2]
                if mutation=='extra':cs['extra']=copy.deepcopy(cs['state'])
                if mutation=='driver':cs['copy_d']['connections']['F']=[5]
                if mutation=='async':cs['copy']['type']='DFFCE'
                with self.assertRaises(ValueError):collapse(a,b,r)

    def test_original_output_corruption_is_not_hidden_by_invariant(self):
        a,b,r=sample();b['modules']['top']['ports']['q']['bits']=['0']
        with tempfile.TemporaryDirectory(dir=ROOT/'results') as tmp:
            with self.assertRaisesRegex(ValueError,'CEC failed'):prove_replicas(a,b,r,Path(tmp)/'bad')

if __name__=='__main__':unittest.main()
