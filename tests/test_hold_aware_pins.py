import itertools,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_hold_aware_pins import permute


def test_every_pin_bijection_preserves_actual_boolean_truth():
    cell=dict(type='LUT4',parameters={'INIT':'0110011010011110'},connections={**{f'I{i}':[i] for i in range(4)},'F':[9]},port_directions={**{f'I{i}':'input' for i in range(4)},'F':'output'})
    for order in itertools.permutations(range(4)):
        candidate=permute(cell,order)
        for assignment in range(16):
            address=sum(((assignment>>candidate['connections']['I'+str(i)][0])&1)<<i for i in range(4))
            assert (int(cell['parameters']['INIT'],2)>>assignment)&1==(int(candidate['parameters']['INIT'],2)>>address)&1
    with pytest.raises(ValueError):permute(cell,[0,0,2,3])
