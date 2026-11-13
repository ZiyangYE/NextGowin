import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ownpr_hard_arrival_edges import load
from ownpr_image_storage import sha

def test_bypass_dsp_unused_clocks_are_not_data_inputs(tmp_path,monkeypatch):
    import ownpr_hard_timing_model
    matrix=[[.1,.2,.1,.2,1.1,1.2,1.1,1.2],[0.]*8]
    class FakeLibrary:
        sources=[]
        def matrices(self,*args):return matrix
    monkeypatch.setattr(ownpr_hard_timing_model,'HardCellTiming',FakeLibrary)
    data=dict(full_data_pins=True,actual_edges=[dict(source='A[0]',destination='DOUT[0]',kind=1,matrices=matrix)])
    file=tmp_path/'queries.json';file.write_text(json.dumps(data))
    index=tmp_path/'hard_timing_index.json';index.write_text(json.dumps(dict(vendor_pnr_executed=False,instances=[dict(instance='own_i_1',file=file.name,sha256=sha(file))])))
    (tmp_path/'manifest.json').write_text(json.dumps(dict(hard_timing_index_sha256=sha(index))))
    cell=dict(type='MULTALU27X18',connections={'A':[1,2],'CLK':[99,99],'DOUT':[3,4]},port_directions={'A':'input','CLK':'input','DOUT':'output'})
    arcs,info=load(tmp_path,{'dsp':cell},{'own_i_1':'dsp'},100,{})
    assert arcs['dsp'][0]['delay']==[1.1,1.2]
    assert info['timed_input_pins']['dsp']==['A[0]']
    assert 'CLK[0]' in info['inactive_input_pins']['dsp']
