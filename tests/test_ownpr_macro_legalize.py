import copy,sys,unittest
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_macro_legalize import clear_site

def sample():
    def lut(i,o):return dict(type='LUT1',parameters={'INIT':'10'},connections={'I0':[i],'F':[o]},port_directions={'I0':'input','F':'output'})
    ff=dict(type='DFFRE',connections=dict(D=[5],Q=[6],CLK=[20],CE=['1'],RESET=['0']),port_directions={p:'output' if p=='Q' else 'input' for p in ('D','Q','CLK','CE','RESET')})
    d={'modules':{'top':{'cells':{'root':lut(2,3),'paired':lut(4,5),'state':ff,'anchor':lut(6,7)}}}}
    p=dict(clusters={'root':dict(lut='root',ff=None,row=0,column=0,slot=0),'paired':dict(lut='paired',ff='state',row=1,column=1,slot=0),'anchor':dict(lut='anchor',ff=None,row=2,column=2,slot=0)},fixed_io={})
    return d,p,SimpleNamespace(rows=3,cols=3,grid=[[17]*3 for _ in range(3)])

class MacroLegalizationTests(unittest.TestCase):
    def test_relocates_pair_as_one_unit_and_preserves_protected_anchors(self):
        d,p,dev=sample();before=copy.deepcopy(p);cost=lambda r,c:100*(abs(r-1)+abs(c-1))
        new,site,report=clear_site(d,p,'root',dev,cost,{'anchor'},radius=1)
        self.assertEqual(site,(1,1));self.assertEqual(p,before);self.assertEqual(report['displaced_registers'],1)
        self.assertEqual(new['clusters']['anchor'],p['clusters']['anchor']);self.assertEqual(new['clusters']['paired']['ff'],'state')
        self.assertIn(new['clusters']['paired']['slot'],(0,2,4));self.assertTrue(report['clock_replan_required'])
        self.assertEqual(len({(v['row'],v['column'],v['slot']) for v in new['clusters'].values()}),len(new['clusters']))

    def test_protected_driver_and_wide_macro_are_never_evicted(self):
        d,p,dev=sample();cost=lambda r,c:100*(abs(r-1)+abs(c-1))
        new,site,report=clear_site(d,p,'root',dev,cost,{'state'})
        self.assertNotEqual(site,(1,1));self.assertEqual(new['clusters']['paired'],p['clusters']['paired'])
        p['mux_clusters']={'macro':dict(row=1,column=1)}
        new,site,report=clear_site(d,p,'root',dev,cost)
        self.assertNotEqual(site,(1,1));self.assertEqual(new['mux_clusters'],p['mux_clusters'])

if __name__=='__main__':unittest.main()
