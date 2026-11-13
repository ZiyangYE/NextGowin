import copy,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from formal_padding_projection import certify,project
from formal_memory_lanes import propose_lanes
from compositional_memory_cut import abstract
from test_formal_memory_lanes import fixture


def test_projection_replaces_only_certified_artificial_padding_monitor():
    gold,gate,groups=fixture();groups=propose_lanes(gold,gate,'top',groups);certificate=certify(gate,'top',groups)
    assert [r['port'] for r in certificate['replaced_observations']]==['ng_mem_0_part_0_padding_write']
    for role,original in [('gold',gold),('gate',gate)]:
        cut,_=abstract(original,'top',groups,role);snapshot=copy.deepcopy(cut);new,receipt=project(cut,'top',certificate,role)
        assert cut==snapshot
        before=cut['modules']['top'];after=new['modules']['top']
        assert before['cells']==after['cells']
        assert set(before['ports'])-set(after['ports'])=={'ng_mem_0_part_0_padding_write'}
        for name in ('clk','address','out','ng_mem_0_part_0_write_data','ng_mem_0_part_0_write_mask','ng_mem_0_part_0_read_address'):
            assert before['ports'][name]==after['ports'][name]
        assert receipt['original_cells_fanouts_and_init_unchanged']


def test_certificate_recomputes_original_array_observability():
    gold,gate,groups=fixture();groups=propose_lanes(gold,gate,'top',groups)
    gate['modules']['top']['ports']['observable_padding']={'direction':'output','bits':[33]}
    with pytest.raises(ValueError,match='functional'):certify(gate,'top',groups)


def test_certificate_rejects_padding_affecting_another_memory_input():
    gold,gate,groups=fixture();groups=propose_lanes(gold,gate,'top',groups)
    gate['modules']['top']['cells']['mem']['connections']['WR_ADDR']=[33]
    with pytest.raises(ValueError,match='functional'):certify(gate,'top',groups)


def test_original_io_cannot_be_removed_using_projection_record():
    gold,gate,groups=fixture();groups=propose_lanes(gold,gate,'top',groups);certificate=certify(gate,'top',groups)
    certificate['replaced_observations'][0]['port']='out'
    cut,_=abstract(gold,'top',groups,'gold')
    with pytest.raises(ValueError,match='artificial'):project(cut,'top',certificate,'gold')
