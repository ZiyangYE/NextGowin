import copy,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_device import Device
from ownpr_lut_slot import move,candidates


@pytest.fixture(scope='module')
def data():
    p=ROOT/'results/ownpr_uart_cluster_swap_003/placement'
    return json.loads((p/'packed.json').read_text()),json.loads((p/'placement.json').read_text()),Device()


def test_free_slot_preserves_logic_clock_and_fixed_io(data):
    d,p,device=data;before=copy.deepcopy((d,p));new,c=move(d,p,device,'ng_acd_2',6)
    assert (d,p)==before and new['fixed_io']==p['fixed_io']
    assert new['clusters']['ng_acd_2']['slot']==6 and c['clock_sink_sites_preserved']
    assert all(new['clusters'][n]==r for n,r in p['clusters'].items() if n!='ng_acd_2')


def test_rejects_paired_occupied_and_macro_slots(data):
    d,p,device=data
    with pytest.raises(ValueError,match='unpaired'):move(d,p,device,'ng_acd_160',6)
    with pytest.raises(ValueError,match='Occupied'):move(d,p,device,'ng_acd_2',0)
    leaf=next(n for n,r in p['clusters'].items() if any((r['row'],r['column'])==(x['row'],x['column']) for x in p['mux_clusters'].values()))
    with pytest.raises(ValueError,match='exclusive'):move(d,p,device,leaf,(p['clusters'][leaf]['slot']+1)%8)


def test_candidates_derive_from_report_without_signal_name_rules(data):
    d,p,device=data;sta=ROOT/'results/ownpr_uart_bs1_closure_001/export/native'
    rows=candidates(d,p,(sta/'probe.tr').read_text(),json.loads((sta/'name_map.json').read_text()))
    assert rows and all(not p['clusters'][r['instance']]['ff'] for r in rows)
    occupied={(c['row'],c['column'],c['slot']) for c in p['clusters'].values()}
    assert all((p['clusters'][r['instance']]['row'],p['clusters'][r['instance']]['column'],r['slot']) not in occupied for r in rows)
