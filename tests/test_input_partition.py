import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_input_partition import select_input

def test_positive_slack_input_selected_and_clock_source_excluded():
    cells={n:dict(type='IBUF',connections={'O':[b]},port_directions={'O':'output'}) for n,b in [('reset',1),('clock',2)]}
    cells['ff']=dict(type='DFFRE',connections={'CLK':[2]},port_directions={'CLK':'input'})
    clusters={}
    for i in range(4):
        n='lut'+str(i);cells[n]=dict(type='LUT1',connections={'I0':[1]},port_directions={'I0':'input'});clusters[n]=dict(row=i,column=2,slot=0)
    placed=dict(fixed_io={'reset':{},'clock':{}},clusters=clusters)
    text='3.1.1 Setup Paths Table\n 1 -5.0 own_i_1/I own_i_3/CLK\n 2 0.028 own_i_2/I own_i_4/I0\n3.1.2 Hold Paths Table\n'
    chosen,ranking=select_input({'modules':{'top':{'cells':cells}}},placed,{'clock':'own_i_1','reset':'own_i_2'},text)
    assert chosen['source']=='reset' and len(chosen['sinks'])==4
    assert len(ranking)==1 and chosen['slack_ns']==0.028
