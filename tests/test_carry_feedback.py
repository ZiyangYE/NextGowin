import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_carry_feedback import rigid_offsets


def test_complete_chain_translation_preserves_slot_and_profile():
    old={n:dict(row=5,column=10+i//6,slot=i%6,mode=2,input_profile=['signal',0,'signal','signal']) for i,n in enumerate('abcdefg')};chains=[dict(cells=list(old))]
    new=copy.deepcopy(old)
    for row in new.values():row['row']+=3;row['column']-=2
    assert rigid_offsets(chains,old,new)[0]['offset']==[3,-2]
    new['g']['row']-=1
    with pytest.raises(ValueError,match='non-rigid'):rigid_offsets(chains,old,new)
    new=copy.deepcopy(old);new['a']['slot']=1
    with pytest.raises(ValueError,match='slot/mode/profile'):rigid_offsets(chains,old,new)


def test_sum_neighbor_copy_preserves_other_receiver_and_avoids_port_bits():
    from ownpr_carry_translate import copy_sum_neighbors
    lut=lambda inputs,out,word:dict(type='LUT'+str(len(inputs)),parameters={'INIT':word},attributes={},port_directions={**{f'I{i}':'input' for i in range(len(inputs))},'F':'output'},connections={**{f'I{i}':[b] for i,b in enumerate(inputs)},'F':[out]})
    cells={'root':lut([20,1],30,'0110'),'near':lut([30],31,'10'),'far':lut([30],32,'10')}
    for i in range(6):cells['a'+str(i)]=dict(type='ALU',connections={'SUM':[20+i]},port_directions={'SUM':'output'},parameters={'ALU_MODE':'10'},attributes={})
    d=dict(modules=dict(top=dict(cells=cells,ports={'unused':dict(direction='input',bits=[999])},netnames={})))
    l=dict(clusters={n:dict(lut=n,ff=None,row=r,column=c,slot=0) for n,r,c in [('root',10,1),('near',1,3),('far',10,2)]},arithmetic_clusters={'a'+str(i):dict(row=1,column=1,slot=i) for i in range(6)},fixed_io={})
    graphs={'setup':dict(edges=[dict(kind='wire',source='a0/SUM',destination='root/I0'),dict(kind='wire',source='root/F',destination='near/I0'),dict(kind='wire',source='root/F',destination='far/I0')])};metrics={'setup':{'slack':{'near/I0':[-1.,-1.],'far/I0':[1.,1.]}}}
    new,placed,changes=copy_sum_neighbors(d,l,dict(cells=['a'+str(i) for i in range(6)]),metrics,graphs)
    assert len(changes)==1 and new['modules']['top']['cells']['far']['connections']['I0']==[30]
    copycell=new['modules']['top']['cells'][changes[0]['copy']]
    assert copycell['parameters']==cells['root']['parameters'] and copycell['connections']['F'][0]>999
    assert new['modules']['top']['cells']['near']['connections']['I0']==copycell['connections']['F'] and placed['clusters'][changes[0]['copy']]['slot']==6
    assert d['modules']['top']['cells']['near']['connections']['I0']==[30]
