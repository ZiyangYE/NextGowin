import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from formal_memory_lanes import propose_lanes,indices
from compositional_memory_cut import profile,initial_relation,abstract,lane_pairs
from formal_relation_candidates import propose


def fixture():
    def model(width,rd,q,word,data,init):
        params={k:format(v,'b') for k,v in dict(WIDTH=width,SIZE=2,ABITS=1,RD_PORTS=1,WR_PORTS=1,OFFSET=0,RD_CLK_ENABLE=0,WR_CLK_ENABLE=1,WR_CLK_POLARITY=1,RD_TRANSPARENCY_MASK=0,RD_COLLISION_X_MASK=0).items()};params['INIT']=init*(width*2)
        con=dict(RD_ADDR=[3],RD_DATA=rd,RD_EN=['1'],WR_ADDR=[3],WR_DATA=data,WR_EN=['1']*width,WR_CLK=[2],RD_ARST=['0'],RD_SRST=['0'])
        mem=dict(type='$mem_v2',parameters=params,connections=con,port_directions={k:'output' if k=='RD_DATA' else 'input' for k in con})
        ff=dict(type='$dff',parameters={'CLK_POLARITY':'1'},connections={'D':rd,'Q':q,'CLK':[2]},port_directions={'D':'input','Q':'output','CLK':'input'})
        ports={'clk':{'direction':'input','bits':[2]},'address':{'direction':'input','bits':[3]},'out':{'direction':'output','bits':[q[0],q[-1] if width==3 else q[1]]}}
        module={'attributes':{'top':'1'},'cells':{'mem':mem,'read_ff':ff},'ports':ports,'netnames':{'read_word':{'hide_name':0,'bits':word,'attributes':{}}}}
        return {'modules':{'top':module}}
    gold=model(3,[10,11,12],[20,21,22],[20,21,22],['1','0','1'],'x')
    gate=model(4,[40,41,42,43],[30,31,32,33],[30,99,31],['1','1','0','0'],'0')
    groups=[dict(gold='mem',profile=profile(gold['modules']['top']['cells']['mem']),spans=[dict(gate='mem',read_port=0,logical_low=0,width=3)])]
    return gold,gate,groups


def test_removed_constant_lane_maps_to_physical_padding_without_dropping_checks():
    g,t,groups=fixture();mapped=propose_lanes(g,t,'top',groups);span=mapped[0]['spans'][0]
    assert span['lane_pairs']==[[0,0],[1,2],[2,1]] and not span['lane_relation_proved']
    assert initial_relation(g,t,'top',mapped)['checks'][0]['logical_bits']==6
    a,ga=abstract(g,'top',mapped,'gold');b,gb=abstract(t,'top',mapped,'gate');tm=b['modules']['top'];oracle=tm['ports']['ng_mem_0_read_0']['bits']
    assert tm['cells']['read_ff']['connections']['D']==[oracle[0],oracle[2],oracle[1],'0']
    assert set(a['modules']['top']['ports'])==set(tm['ports'])
    assert 'ng_mem_0_part_0_padding_write' in tm['ports']
    assert len(ga['command_obligations'])==len(gb['command_obligations'])==1
    for m in (a['modules']['top'],tm):
        out=m['ports']['ng_mem_0_part_0_write_data']['bits']
        cell=next(c for c in m['cells'].values() if c['connections'].get('Y')==out)
        assert cell['connections']['A']==['1','0','1']


def test_live_undriven_bit_is_not_treated_as_removed_constant():
    g,t,groups=fixture();t['modules']['top']['cells']['use']=dict(type='$not',connections={'A':[99],'Y':[100]},port_directions={'A':'input','Y':'output'})
    with pytest.raises(ValueError,match='Missing nonconstant'):propose_lanes(g,t,'top',groups)


def test_conflicting_aliases_reject_instead_of_selecting_a_convenient_mapping():
    g,t,groups=fixture();g['modules']['top']['netnames']['other']={'hide_name':0,'bits':[20,21,22]};t['modules']['top']['netnames']['other']={'hide_name':0,'bits':[31,99,30]}
    with pytest.raises(ValueError,match='Conflicting'):propose_lanes(g,t,'top',groups)


def test_defined_source_init_cannot_be_changed_to_match_padding():
    g,t,groups=fixture();mapped=propose_lanes(g,t,'top',groups);g['modules']['top']['cells']['mem']['parameters']['INIT']='111111'
    with pytest.raises(ValueError,match='INIT mismatch'):initial_relation(g,t,'top',mapped)


def test_nonbijective_or_out_of_range_lane_mapping_rejects():
    for pairs in ([[0,0],[1,0]],[[0,0],[1,5]]):
        with pytest.raises(ValueError):lane_pairs(dict(width=2,logical_low=0,lane_pairs=pairs),2,2)


def test_coordinate_alignment_handles_offset_and_ascending_declarations():
    assert indices({'bits':[8,9,10],'offset':5})==[5,6,7]
    assert indices({'bits':[8,9,10],'offset':5,'upto':1})==[7,6,5]
    g,t,_=fixture();a=g['modules']['top'];b=t['modules']['top']
    a['netnames']={'word':dict(bits=[20,21,22],offset=4,hide_name=0)}
    b['netnames']={'word':dict(bits=[31,30],offset=5,upto=1,hide_name=0)}
    candidates=propose(a,b)
    row=next(x for x in candidates if x['name']=='word')
    assert row['gold']==[21,22] and row['gate']==[30,31]


def test_padding_noninterference_checks_bit_lanes_through_vector_registers():
    from formal_padding_observability import audit
    g,t,groups=fixture();mapped=propose_lanes(g,t,'top',groups)
    assert audit(t['modules']['top'],mapped)['status']=='padding_read_noninterference_proved'
    t['modules']['top']['ports']['poison']={'direction':'output','bits':[33]}
    assert audit(t['modules']['top'],mapped)['status']=='padding_reaches_functional_target'


def test_hidden_pipeline_predecessors_are_candidates_without_rewiring():
    from formal_relation_candidates import extend_predecessors
    def module(first,last):
        cells={}
        for name,q,d in [('hidden',first,2),('observed',last,first)]:
            cells[name]={'type':'$dffe','connections':{'Q':[q],'D':[d],'CLK':[3],'EN':[4]},'parameters':{'CLK_POLARITY':'1','EN_POLARITY':'1'}}
        return {'ports':{'q':{'direction':'output','bits':[last]}},'cells':cells}
    gold=module(10,11);gate=module(20,21);before=copy.deepcopy((gold,gate))
    rows,added=extend_predecessors(gold,gate,[])
    assert (gold,gate)==before and len(added)==1
    assert added[0]['gold']==[10] and added[0]['gate']==[20] and added[0]['proven'] is False
    assert extend_predecessors(gold,gate,[])[0]==rows
    # A non-state expression is not silently treated as a corresponding FF.
    gate['cells']['observed']['connections']['D']=[99]
    assert extend_predecessors(gold,gate,[])[1]==[]
