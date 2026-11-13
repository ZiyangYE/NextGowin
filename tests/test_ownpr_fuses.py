import json,sys,unittest,copy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_fuse_audit import SwitchChoices,audit
from ownpr_device import Device

@unittest.skipUnless((ROOT/'results/ownpr_int2float_route_003/routes.json').exists(),'Requires native-only routed fixture')
class SwitchConfigurationTests(unittest.TestCase):
    def test_real_switch_choices_and_incompatible_alternative(self):
        routes=json.loads((ROOT/'results/ownpr_int2float_route_003/routes.json').read_text());choices=SwitchChoices(Device())
        self.assertEqual(audit(routes,choices)['status'],'consistent')
        for net in routes['nets'].values():
            for pip in net['pips']:
                r,c,src,dst=pip;template=choices.template(choices.device.grid[r][c]);old=choices.requirements(pip)
                for other_source,other_dest in template:
                    if other_dest!=dst or other_source==src:continue
                    alternative=(r,c,other_source,dst);new=choices.requirements(alternative)
                    if any(old[b]!=new[b] for b in old.keys()&new.keys()):
                        bad=copy.deepcopy(routes);bad['nets']['corrupted_choice']={'pips':[alternative]}
                        self.assertEqual(audit(bad,choices)['status'],'conflicting');return
        self.fail('Fixture had no distinguishable switch choices')
if __name__=='__main__':unittest.main()
