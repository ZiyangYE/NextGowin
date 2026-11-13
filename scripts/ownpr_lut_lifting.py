"""Boolean-preserving LUT width extension; no extra physical LUT is used."""
import copy


def independent(cell,pin):
    width=int(cell['type'][-1]);truth=int(cell['parameters']['INIT'],2)
    if cell['type'] not in ('LUT1','LUT2','LUT3','LUT4') or pin not in range(width):raise ValueError('Invalid LUT independence query')
    return all(((truth>>a)&1)==((truth>>(a^(1<<pin)))&1) for a in range(1<<width))


def lift(cell,ground_bit):
    width=int(cell['type'][-1])
    if cell['type'] not in ('LUT1','LUT2','LUT3','LUT4'):raise ValueError('Only ordinary LUTs can be lifted')
    result=copy.deepcopy(cell)
    if width==4:return result
    truth=cell['parameters']['INIT']
    if len(truth)!=(1<<width) or set(truth)-{'0','1'}:raise ValueError('Invalid lifted LUT truth table')
    original=int(truth,2);word=sum(((original>>(a&((1<<width)-1)))&1)<<a for a in range(16))
    result['type']='LUT4';result['parameters']['INIT']=format(word,'016b')
    result.setdefault('attributes',{})['nextgowin_lifted_lut_width']=format(width,'b')
    for p in range(width,4):
        result['connections']['I'+str(p)]=[ground_bit];result['port_directions']['I'+str(p)]='input'
    return result
