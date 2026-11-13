import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_selector_cascade import replace
from ownpr_mux_macros import tree


def evaluate(design,root,values):
    cells=design['modules']['top']['cells'];drivers={bs[0]:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output'}
    known=dict(values)
    for c in cells.values():
        if c['type'] in ('GND','VCC'):known[next(iter(c['connections'].values()))[0]]=int(c['type']=='VCC')
    def cell_value(name):
        c=cells[name];p=c['connections']
        def value(b):return known[b] if b in known else cell_value(drivers[b])
        if c['type'].startswith('LUT'):return int(c['parameters']['INIT'],2)>>sum(value(p['I'+str(i)][0])<<i for i in range(int(c['type'][-1])))&1
        return value(p['I'+str(value(p['S0'][0]))][0])
    return cell_value(root)


@pytest.mark.parametrize('width',[1,2,3])
def test_all_small_boolean_functions_and_late_input_orders(width):
    for truth in range(1<<(1<<width)):
        cell=dict(type='LUT'+str(width),parameters={'INIT':format(truth,f'0{1<<width}b')},attributes={},
            connections={**{'I'+str(i):[i+2] for i in range(width)},'F':[20]},port_directions={**{'I'+str(i):'input' for i in range(width)},'F':'output'})
        design={'modules':{'top':{'cells':{'f':cell},'ports':{},'netnames':{}}}}
        if truth in (0,(1<<(1<<width))-1):
            with pytest.raises(ValueError,match='independent'):replace(design,'f')
            continue
        converted,info=replace(design,'f')
        for late in info['support']:
            for count in range(1,len(info['support'])+1):
                candidate,meta=replace(design,'f',late,count);tree(candidate,'f')
                assert meta['selector_order'][-1]==late and meta['lut_growth']==(1<<count)-1
                for assignment in range(1<<width):
                    values={i+2:assignment>>i&1 for i in range(width)}
                    assert evaluate(design,'f',values)==evaluate(candidate,'f',values)


@pytest.mark.parametrize('truth',[0x6996,0x8000,0xface,0x1234])
def test_four_input_cofactors_preserve_all_assignments(truth):
    cell=dict(type='LUT4',parameters={'INIT':format(truth,'016b')},attributes={},
        connections={**{'I'+str(i):[i+2] for i in range(4)},'F':[20]},port_directions={**{'I'+str(i):'input' for i in range(4)},'F':'output'})
    design={'modules':{'top':{'cells':{'f':cell},'ports':{},'netnames':{}}}}
    _,base=replace(design,'f',selector_count=1)
    for count in range(1,min(3,len(base['support']))+1):
        for late in base['support']:
            after,info=replace(design,'f',late,count);tree(after,'f')
            assert len(info['data_order'])+len(info['selector_order'])==len(base['support'])
            for a in range(16):
                values={i+2:a>>i&1 for i in range(4)}
                assert evaluate(design,'f',values)==evaluate(after,'f',values)


def test_real_lifted_lut_support_and_hidden_wire_ids_are_preserved():
    p=ROOT/'results/ownpr_uart_branch_pin_closure_002/round_001/eco/placement/packed.json';design=json.loads(p.read_text());old=copy.deepcopy(design)
    design['modules']['top']['netnames']['unused_debug_name']={'bits':[100000],'attributes':{}}
    after,info=replace(design,'ng_acd_0',213)
    assert info['selector_order']==[211,213] and info['lut_growth']==3 and info['mux_count']==3
    for assignment in range(4):
        values={211:assignment&1,213:assignment>>1&1}
        assert evaluate(design,'ng_acd_0',values)==evaluate(after,'ng_acd_0',values)
    for name in info['added_cells']:
        if name!='ng_acd_0':assert after['modules']['top']['cells'][name]['connections']['F' if after['modules']['top']['cells'][name]['type']=='LUT4' else 'O'][0]>100000
    assert old['modules']['top']['cells']==design['modules']['top']['cells']
