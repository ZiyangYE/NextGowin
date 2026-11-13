import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json
from ownpr_pin_cut_selection import measured_inputs,rebuild_receivers


def test_reconstructed_receivers_match_the_independently_saved_binding():
    f=ROOT/'results/vexriscv_shared_global_001';routes=f/'implementation/route'
    rows,_=rebuild_receivers(routes,f/'candidate',load_json(routes/'manifest.json'))
    saved=load_json(routes/'search_weights.json')['policy']['receivers']
    flatten=lambda rs:{(r['net'],r['node'],tuple(pin)) for r in rs for pin in r['receivers']}
    assert flatten(rows)==flatten(saved)


def test_hold_repair_without_search_weights_has_current_loaded_measurements():
    f=ROOT/'results/vexriscv_shared_global_001';routes=f/'reconciled_001/route';placement=f/'refinement_001/hold/round_000/placement'
    values,source=measured_inputs(routes,f/'reconciled_001/export/wire',placement)
    assert len(values)==15495 and 'reconstructed_receiver_binding' in source
    assert values['IBusCachedPlugin_cache.ng_memory_0.0.0','ADB[11]']==pytest.approx(.9067500233650208)
    meta=load_json(routes/'manifest.json')
    with pytest.raises(ValueError,match='placement differs'):rebuild_receivers(routes,ROOT/'results/vexriscv_pin_frontier_001/candidate',meta)
    with pytest.raises(ValueError,match='Hard receiver binding changed'):rebuild_receivers(routes,placement,dict(meta,hard_endpoint_audit_sha256='0'*64))
