import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha


def test_real_cpu_batch_prunes_only_truth_independent_pins_with_complete_cec():
    before=ROOT/'results/vexriscv_control_batch_place_001';after=ROOT/'results/vexriscv_control_pruned_place_002'
    old=load_json(before/'packed.json')['modules']['top'];new=load_json(after/'packed.json')['modules']['top'];meta=load_json(after/'manifest.json')
    assert sha(before/'placement.json')==sha(after/'placement.json') and old['ports']==new['ports']
    assert set(old['cells'])==set(new['cells']) and meta['prune_unused_requested']
    changes=meta['unused_input_pruning'];assert len(changes)==22 and len({r['instance'] for r in changes})==11
    expected={}
    for row in changes:
        name,pin=row['instance'],row['pin'];a=old['cells'][name];b=new['cells'][name];width=int(a['type'][-1]);index=int(pin[1:]);truth=int(a['parameters']['INIT'],2)
        assert a['parameters']==b['parameters'] and a['connections'][pin]==[row['old_bit']]
        assert b['connections'][pin]==[row['constant_bit']] and row['constant_value']==0
        assert all((truth>>v&1)==(truth>>(v^(1<<index))&1) for v in range(1<<width))
        expected.setdefault(name,set()).add(pin)
    for name,a in old['cells'].items():
        b=new['cells'][name]
        if name not in expected:assert a==b
        else:
            assert all(a[k]==b[k] for k in a if k!='connections')
            assert all(a['connections'][pin]==b['connections'][pin] for pin in a['connections'] if pin not in expected[name])
    assert all(p['cec']['equivalent'] and p['observed_hard_inputs_and_ports']==8584 for p in meta['proof'])
    assert meta['producer_source_sha256']['prune_ownpr_lut_inputs.py']==sha(after/'prune_ownpr_lut_inputs.py.txt')
