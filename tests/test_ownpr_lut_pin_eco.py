import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_lut_pin_eco import permute
from audit_ownpr_native_export import audit


def test_truth_permutation_exhaustive_basis_functions():
    for width in (2,3,4):
        for truth_bit in range(1<<width):
            cell=dict(type='LUT'+str(width),parameters={'INIT':format(1<<truth_bit,f'0{1<<width}b')},connections={f'I{i}':[i] for i in range(width)})
            for a in range(width):
                for b in range(a+1,width):
                    changed=permute(cell,a,b)
                    for stimulus in range(1<<width):
                        addr=sum(((stimulus>>changed['connections'][f'I{i}'][0])&1)<<i for i in range(width))
                        assert ((int(changed['parameters']['INIT'],2)>>addr)&1)==int(stimulus==truth_bit)
                    assert permute(changed,a,b)==cell


def test_actual_pin_eco_closes_fullio_setup_and_hold():
    candidate=ROOT/'results/ownpr_int2float_pin_eco_002';meta=json.loads((candidate/'manifest.json').read_text())
    assert meta['cec'] and all(p['cec']['equivalent'] for p in meta['cec'])
    result=audit(ROOT/'results/native_ownpr_export_015',placement=candidate/'placement')
    assert result['timing']['setup_hold_passed']
    assert result['timing']['path_coverage']['setup']['worst_slack_ns']==.246
    assert result['io']['pins_preserved'] and result['io']['ports']==19
    assert result['configuration_bits_verified']==11826
    assert not result['full_bitstream_verified']
