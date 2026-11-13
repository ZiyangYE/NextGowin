import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_bidir_timing import analyze
from ownpr_global_timing_frontier import select_roots


def test_frontier_covers_regions_outside_worst_path_window():
    cells={n:dict(type='LUT4',connections={'F':[i]}) for i,n in enumerate(('a','b','c'))}
    cells.update({n:dict(type='DFFCE',connections={}) for n in ('f1','f2')})
    design=dict(modules={'top':dict(cells=cells)})
    layout=dict(clusters={'a':dict(row=1,column=1,ff='f1'),'b':dict(row=2,column=2),'c':dict(row=40,column=40,ff='f2')},fixed_io={})
    graph=dict(nodes=['a/F','b/F','c/F','f1/D','f2/D'],seeds={'a/F':[10.,10.],'b/F':[9.,9.],'c/F':[1.,1.]},edges=[dict(source=n+'/F',destination=d+'/D',delay=[1.,1.],transitions=[[0,0],[1,1]],kind='wire') for n,d in [('a','f1'),('b','f1'),('c','f2')]])
    metrics=analyze(graph,{'f1/D':[5.,5.],'f2/D':[5.,5.]})
    roots,audit=select_roots(design,layout,graph,metrics,['a','b','c'],limit=2)
    assert roots==['a','c']
    assert not audit['unrepresented_reachable_groups']
    roots,audit=select_roots(design,layout,graph,metrics,['a','b','c'],limit=1)
    assert len(audit['unrepresented_reachable_groups'])==1
