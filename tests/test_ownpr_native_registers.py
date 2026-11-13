import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_device import Device


class NativeRegisterTests(unittest.TestCase):
    def test_full_native_control_group_mapping(self):
        ports=Device().register_bindings()
        self.assertEqual(len(ports),8)
        self.assertEqual([ports[i]['CLK'] for i in range(8)],
                         ['CLK0','CLK0','CLK1','CLK1','CLK2','CLK2','CLK2','CLK2'])
        self.assertEqual(ports[7]['D'],'DI7')
        self.assertEqual(ports[6]['CE'],'CE2')
        self.assertEqual(ports[6]['RESET'],'LSR2')


if __name__=='__main__':unittest.main()
