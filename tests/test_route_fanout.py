"""Check route-pressure accounting at hierarchy and constant boundaries."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from analyze_route_fanout import analyze


def leaf(kind,connections,directions):
    return dict(type=kind,connections=connections,port_directions=directions)


class RouteFanoutTests(unittest.TestCase):
    def test_hierarchy_ports_are_not_extra_sinks_and_instances_are_distinct(self):
        # Two instances share one source. Only their physical FF pins count.
        child=dict(attributes={},ports=dict(en=dict(direction='input',bits=[2])),netnames={},
            cells={f'f{i}':leaf('DFFRE',dict(CE=[2],D=['0'],CLK=['0'],RESET=['0'],Q=[i+10]),
                  dict(CE='input',D='input',CLK='input',RESET='input',Q='output')) for i in range(3)})
        top=dict(attributes={},ports={},netnames=dict(enable=dict(bits=[7])),cells=dict(
            a=leaf('child',dict(en=[7]),dict(en='input')),
            b=leaf('child',dict(en=[7]),dict(en='input'))))
        report=analyze(dict(modules=dict(top=top,child=child)),'top',2)
        self.assertEqual(report['leaf_cells'],6)
        self.assertEqual(report['high_fanout_nets'],1)
        row=report['nets'][0]
        self.assertEqual((row['sink_pins'],row['sink_cells']),(6,6))
        self.assertEqual(row['roles'],dict(enable=6))
        self.assertIn('enable',row['aliases'])
        focused=analyze(dict(modules=dict(top=top,child=child)),'top',1,'^enable$')
        self.assertEqual(focused['nets'][0]['sink_pins'],6)
        self.assertEqual(analyze(dict(modules=dict(top=top,child=child)),'top',1,'absent')['nets'],[])

    def test_parent_constant_crossing_hierarchy_is_excluded(self):
        child=dict(attributes={},ports=dict(a=dict(direction='input',bits=[2])),netnames={},
            cells={f'l{i}':leaf('LUT2',dict(I0=[2],I1=[2],F=[i+10]),dict(I0='input',I1='input',F='output')) for i in range(4)})
        top=dict(attributes={},ports={},netnames={},cells=dict(
            c=leaf('child',dict(a=['1']),dict(a='input'))))
        report=analyze(dict(modules=dict(top=top,child=child)),'top',2)
        self.assertEqual(report['leaf_cells'],4)
        self.assertEqual(report['high_fanout_nets'],0)


if __name__=='__main__':unittest.main()
