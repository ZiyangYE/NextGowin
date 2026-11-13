import copy,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import ownpr_mux_architecture as model
from ownpr_image_storage import load_json

FOLDER=ROOT/'results/vexriscv_timing_wide_trial_001/export/native'
PLACE=ROOT/'results/vexriscv_timing_wide_place_001'


def context():return (load_json(PLACE/'packed.json'),load_json(PLACE/'placement.json'),load_json(FOLDER/'name_map.json'))


def test_real_unreported_cpu_macro_requires_complete_calibrated_query_evidence():
    with pytest.raises(ValueError,match='Empty mux'):model.report_audit(FOLDER,require_internal_rows=False)
    with pytest.raises(ValueError,match='Empty mux'):model.report_audit(FOLDER,capture_context=context())
    report=model.report_audit(FOLDER,require_internal_rows=False,capture_context=context())
    assert report['cell_arcs']==report['internal_net_rows']==0
    assert not report['cell_arcs_covered_by_report'] and not report['internal_edges_covered_by_report']
    assert report['unreported_capture_verified']['muxes']==3
    assert report['unreported_capture_verified']['query_vectors']==27
    assert report['unreported_capture_verified']['direct_timing_edges']==6


@pytest.mark.parametrize('mutation',['missing_mux','missing_edge','wrong_delay','missing_query'])
def test_unreported_macro_does_not_hide_missing_or_incorrect_native_data(monkeypatch,mutation):
    read=model.read
    def changed(path):
        data=read(path)
        if Path(path)==FOLDER/'mux_timing_queries.json':
            data=copy.deepcopy(data)
            if mutation=='missing_mux':data['muxes'].pop()
            elif mutation=='missing_edge':data['muxes'][0]['links'][0]['timing_edge_present']=False
            elif mutation=='wrong_delay':data['muxes'][0]['queries'][0]['values'][0]+=.01
            else:data['muxes'][0]['queries'].pop()
        return data
    monkeypatch.setattr(model,'read',changed)
    with pytest.raises(ValueError):model.report_audit(FOLDER,require_internal_rows=False,capture_context=context())
