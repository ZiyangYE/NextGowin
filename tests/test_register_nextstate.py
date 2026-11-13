import copy
import itertools
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from register_nextstate_map import fold
from register_control_map import logic


def sample(async_reset=False):
    cells={}
    for name,ins,out in [('first',[3,4],10),('second',[10,3],11)]:
        cells[name]=dict(type='LUT2',parameters={'INIT':'0110'},attributes={},connections=dict(I0=[ins[0]],I1=[ins[1]],F=[out]),port_directions=dict(I0='input',I1='input',F='output'))
    control='CLEAR' if async_reset else 'RESET'
    cells['state']=dict(type='DFFCE' if async_reset else 'DFFRE',parameters={'INIT':'1'},attributes={},
                       connections=dict(D=[11],CLK=[2],CE=[5],Q=[30],**{control:[6]}),
                       port_directions=dict(D='input',CLK='input',CE='input',Q='output',**{control:'input'}))
    return dict(modules={'top':dict(attributes={},cells=cells,netnames={},ports={**{n:dict(direction='input',bits=[i]) for n,i in [('clk',2),('x',3),('y',4),('ce',5),('rst',6)]},'q':dict(direction='output',bits=[30])})})


def next_value(module,values):
    values=dict(values);values.update({'0':0,'1':1});pending={n:c for n,c in module['cells'].items() if logic(c)}
    while pending:
        ready=[]
        for n,c in pending.items():
            ins,out,truth=logic(c)
            if all(b in values for b in ins):values[out]=truth>>sum(values[b]<<i for i,b in enumerate(ins))&1;ready.append(n)
        if not ready:raise AssertionError('Cycle in independent test evaluator')
        for n in ready:del pending[n]
    ff=module['cells']['state'];p=ff['connections'];active=ff['type'] in ('DFFSE','DFFPE')
    ctrl=next(p[k][0] for k in ('RESET','SET','CLEAR','PRESET') if k in p)
    return int(active) if values[ctrl] else values[p['D'][0]] if values[p['CE'][0]] else values[p['Q'][0]]


class RegisterNextStateTests(unittest.TestCase):
    def test_exact_next_state_with_enable_reset_feedback_and_initial_state(self):
        source=sample();original=copy.deepcopy(source);after,record=fold(source)
        self.assertEqual(source,original);self.assertGreater(record['rewrites'],0)
        old=source['modules']['top']['cells']['state'];new=after['modules']['top']['cells']['state']
        self.assertEqual(old['connections']['CLK'],new['connections']['CLK'])
        self.assertEqual(old['connections']['Q'],new['connections']['Q'])
        self.assertEqual(new['parameters']['INIT'],'1')
        self.assertEqual(sum(c['type'].startswith('DFF') for c in after['modules']['top']['cells'].values()),1)
        for assignment in itertools.product((0,1),repeat=5):
            values=dict(zip((3,4,5,6,30),assignment))
            self.assertEqual(next_value(source['modules']['top'],values),next_value(after['modules']['top'],values))

    def test_active_asynchronous_control_is_protected(self):
        source=sample(True);after,record=fold(source)
        self.assertEqual(record['rewrites'],0);self.assertEqual(after,source)

    def test_user_kept_logic_is_not_removed(self):
        source=sample();source['modules']['top']['cells']['first']['attributes']['keep']='1'
        after,_=fold(source)
        self.assertEqual(after['modules']['top']['cells']['first'],source['modules']['top']['cells']['first'])


if __name__=='__main__':unittest.main()
