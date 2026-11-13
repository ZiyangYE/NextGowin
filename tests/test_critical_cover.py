import sys,itertools
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_critical_cover import minimum_cover,covers,critical_graph,partition_copy,compose_copies
from ownpr_bidir_timing import analyze


def test_reconvergent_paths_need_coordinated_copies_and_uneditable_path_blocks():
    edges=[('s','a'),('s','b'),('a','t'),('b','t')];v={'s','a','b','t'}
    assert minimum_cover(v,edges,{'s'},{'t'},{'a','b'})==['a','b']
    assert minimum_cover(v,edges,{'s'},{'t'},{'a'}) is None
    assert not covers(edges,{'s'},{'t'},{'a'})
    assert minimum_cover(v,edges,{'s'},{'t'},{'s','a','b'})==['s']


def test_small_dag_cover_matches_independent_exhaustive_subset_search():
    v={'s','a','b','c','t'};edges=[('s','a'),('s','b'),('a','c'),('b','c'),('a','t'),('c','t')]
    for mask in range(8):
        eligible={n for i,n in enumerate(('a','b','c')) if mask>>i&1};got=minimum_cover(v,edges,{'s'},{'t'},eligible)
        candidates=[set(c) for k in range(len(eligible)+1) for c in itertools.combinations(sorted(eligible),k) if covers(edges,{'s'},{'t'},c)]
        assert (got is None)==(not candidates)
        if got is not None:assert len(got)==min(map(len,candidates))


def test_nearcritical_graph_includes_hidden_competing_rise_fall_branch():
    graph=dict(nodes=['a','b','t'],seeds={'a':[1.,1.],'b':[1.,1.]},edges=[dict(source='a',destination='t',delay=[1.,.1],transitions=[(0,0),(1,1)]),dict(source='b',destination='t',delay=[.1,1.],transitions=[(0,0),(1,1)])])
    for e in graph['edges']:e['kind']='wire'
    metrics=analyze(graph,{'t':[2.,2.]});v,edges,sources,sinks,_=critical_graph(graph,metrics,0.)
    assert sources=={'a','b'} and edges=={('a','t'),('b','t')}
    assert minimum_cover(v,edges,sources,sinks,{'a'}) is None
    with pytest.raises(ValueError):minimum_cover(v,edges,set(),sinks,{'a'})


def test_partition_copy_keeps_unselected_load_and_exact_truth_without_state_change():
    import copy
    def lut(inputs,out,truth):
        k=len(inputs);return dict(type='LUT'+str(k),parameters={'INIT':format(truth,f'0{1<<k}b')},attributes={},port_directions={**{f'I{i}':'input' for i in range(k)},'F':'output'},connections={**{f'I{i}':[v] for i,v in enumerate(inputs)},'F':[out]})
    d=dict(modules={'top':dict(cells={'x':lut([1,2],3,6),'a':lut([3],4,2),'b':lut([3],5,2)},ports={},netnames={})})
    l=dict(clusters={n:dict(lut=n,ff=None,row=0,column=0,slot=i) for i,n in enumerate(('x','a','b'))},fixed_io={})
    old=copy.deepcopy(d);new,placed,receipt=partition_copy(d,l,'x',[('a','I0',0)],[1,1,0],'test')
    name=receipt['copy'];assert new['modules']['top']['cells'][name]['parameters']==d['modules']['top']['cells']['x']['parameters']
    assert new['modules']['top']['cells'][name]['connections']['I0']==[1]
    assert new['modules']['top']['cells']['a']['connections']['I0']!=[3]
    assert new['modules']['top']['cells']['b']==d['modules']['top']['cells']['b'] and d==old
    with pytest.raises(ValueError,match='two nonempty'):partition_copy(d,l,'x',[('a','I0',0),('b','I0',0)],[1,1,0],'bad')


@pytest.mark.parametrize('reverse',[False,True])
def test_dependent_cut_actions_reconnect_child_copy_independent_of_name_order(reverse):
    def lut(inputs,out,truth):
        k=len(inputs);return dict(type='LUT'+str(k),parameters={'INIT':format(truth,f'0{1<<k}b')},attributes={},port_directions={**{f'I{i}':'input' for i in range(k)},'F':'output'},connections={**{f'I{i}':[v] for i,v in enumerate(inputs)},'F':[out]})
    cells={'z_parent':lut([1],10,2),'a_child':lut([10,2],11,6),'parent_side':lut([10],12,2),'critical':lut([11],13,2),'child_side':lut([11],14,2)}
    d=dict(modules={'top':dict(cells=cells,ports={},netnames={})});l=dict(clusters={n:dict(lut=n,ff=None,row=i,column=0,slot=0) for i,n in enumerate(cells)},fixed_io={})
    actions=[dict(root='z_parent',receivers=[('a_child','I0',0)],site=[7,7,0],tag='p'),dict(root='a_child',receivers=[('critical','I0',0)],site=[8,8,0],tag='c')]
    nd,nl,changes,origins=compose_copies(d,l,list(reversed(actions)) if reverse else actions);by={r['root']:r['copy'] for r in changes};c=nd['modules']['top']['cells']
    assert c[by['a_child']]['connections']['I0']==c[by['z_parent']]['connections']['F']
    assert c['a_child']['connections']['I0']==[10] and c['parent_side']==cells['parent_side']
    for x,y in itertools.product((0,1),repeat=2):
        def evaluate(design):
            values={1:x,2:y};pending=dict(design['modules']['top']['cells'])
            while pending:
                ready=[]
                for n,cell in pending.items():
                    ins=[cell['connections']['I'+str(i)][0] for i in range(int(cell['type'][-1]))]
                    if all(b in values for b in ins):values[cell['connections']['F'][0]]=(int(cell['parameters']['INIT'],2)>>sum(values[b]<<i for i,b in enumerate(ins)))&1;ready.append(n)
                assert ready
                for n in ready:del pending[n]
            return values
        old,new=evaluate(d),evaluate(nd);assert all(old[b]==new[b] for b in (10,11,12,13,14))
    from ownpr_path_cone import prioritize
    metric=dict(setup=dict(wns=-1.,slack={'a_child/I1':[5.,5.]}))
    result=prioritize([dict(name='n',sinks=[7])],[dict(instance=by['a_child'],pin='I1',net='n',node=7)],metric,dict(input_origins=origins))
    assert result[0]['sink_criticalities'][7]==pytest.approx(.05)
