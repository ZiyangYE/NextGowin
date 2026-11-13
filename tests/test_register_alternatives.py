import copy
import itertools
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from register_alternatives import add,select
from test_register_nextstate import sample,next_value


class RegisterAlternativeTests(unittest.TestCase):
    def test_original_state_and_every_next_state_are_preserved(self):
        source=sample();snapshot=copy.deepcopy(source);mapped,record=add(source)
        self.assertEqual(source,snapshot);self.assertEqual(len(record['choices']),1)
        m=mapped['modules']['top'];r=record['choices'][0]
        self.assertEqual(m['cells']['state'],source['modules']['top']['cells']['state'])
        # Independently interpret the actual generated muxes as LUT3s.
        for c in m['cells'].values():
            if c['type']=='$_MUX_':
                p=c['connections'];c.update(type='LUT3',parameters={'INIT':'11001010'},
                    connections=dict(I0=p['A'],I1=p['B'],I2=p['S'],F=p['Y']),
                    port_directions=dict(I0='input',I1='input',I2='input',F='output'))
        m['cells']['state']=r['alternative']
        for a in itertools.product((0,1),repeat=5):
            values=dict(zip((3,4,5,6,30),a))
            self.assertEqual(next_value(source['modules']['top'],values),next_value(m,values))
        self.assertEqual(m['cells']['state']['parameters']['INIT'],'1')

    def test_live_area_recovery_selects_proved_fast_cover(self):
        source=sample();m=source['modules']['top'];m['cells']={n:c for n,c in m['cells'].items() if n=='state'}
        bit=3
        for i in range(4):
            out=10+i;m['cells']['inv'+str(i)]=dict(type='LUT1',parameters={'INIT':'01'},attributes={},
                connections=dict(I0=[bit],F=[out]),port_directions=dict(I0='input',F='output'));bit=out
        m['cells']['state']['connections']['D']=[bit]
        mapped,record=add(source);r=record['choices'][0];m=mapped['modules']['top']
        for n in list(m['cells']):
            if n.startswith('ng_register_choice_'):del m['cells'][n]
        # Four inversions cancel; complete next state fits a single LUT4.
        truth=sum((0 if a>>2&1 else (a&1) if a>>1&1 else a>>3&1)<<a for a in range(16))
        m['cells']['ng_acd_alternative']=dict(type='LUT4',parameters={'INIT':format(truth,'016b')},attributes={'ng_generated':'1'},
            connections=dict(I0=[3],I1=[5],I2=[6],I3=[30],F=r['alternative']['connections']['D']),
            port_directions=dict(I0='input',I1='input',I2='input',I3='input',F='output'))
        selected,report=select(mapped,record,area_allowance=0,control_penalty_ps=206)
        self.assertEqual(report['timing_costs']['control_routing_penalty_ps'],206)
        self.assertEqual(report['selected'],1);self.assertEqual(report['modules'][0]['final_live_luts'],1)
        self.assertFalse(any(n.startswith('inv') for n in selected['modules']['top']['cells']))
        for a in itertools.product((0,1),repeat=5):
            values=dict(zip((3,4,5,6,30),a))
            self.assertEqual(next_value(source['modules']['top'],values),next_value(selected['modules']['top'],values))

    def test_active_async_controls_and_kept_state_are_excluded(self):
        for source in (sample(True),sample()):
            if source['modules']['top']['cells']['state']['type']=='DFFRE':
                source['modules']['top']['cells']['state']['attributes']['keep']='1'
            mapped,record=add(source)
            self.assertEqual(mapped,source);self.assertEqual(record['choices'],[])

    def test_nonfinite_and_negative_control_penalty_are_rejected(self):
        for penalty in (-1,float('nan'),float('inf'),True):
            with self.subTest(penalty=penalty):
                with self.assertRaises(ValueError):select(sample(),{'choices':[]},control_penalty_ps=penalty)


if __name__=='__main__':unittest.main()
