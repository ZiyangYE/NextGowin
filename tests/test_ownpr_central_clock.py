import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_device import Device
from ownpr_clock_graph import ClockGraph
from ownpr_clock_configuration import ClockChoices
from ownpr_lut_configuration import NativeFrameProjection


def test_central_selector_matches_native_and_own_images():
    device=Device();graph=ClockGraph(ROOT/'results/native_clock_vex_input_002');model=ClockChoices(device,graph);projection=NativeFrameProjection(device)
    bits,kind=model.edge([-2,-1,133],[-1,-1,16])
    assert kind=='central_p30_table38' and bits and {p[:2] for p in bits}=={(54,88)}
    for folder,name in [('native_vexriscv_clock_reference_001','probe.fs'),('vexriscv_clock_encoding_001','encoding_clock.fs')]:
        image=projection.read(ROOT/'results'/folder/name)
        assert all(int(image[projection.index(p)])-48==value for p,value in bits.items())
    for a,b in [([-1,-1,16],[-1,-1,297]),([-1,-1,297],[-1,-2,245]),([-1,-1,297],[-2,-1,245])]:
        assert model.edge(a,b)==({},'fixed_central_clock_copy')


def test_input_prefix_representation_preserves_measured_clock_delays():
    p=ROOT/'results/vexriscv_clock_prefix_timing_001';m=json.loads((p/'manifest.json').read_text());r=json.loads((p/'comparison.json').read_text())
    from ownpr_image_storage import sha
    assert m['status']=='captured' and m['vendor_pnr_executed'] is False
    assert all(sha(p/n)==digest for n,digest in m['output_sha256'].items())
    assert r['sinks']==1546 and r['changed_sinks']==0 and not r['full_sta_verified']


def test_central_candidate_reproduces_all_sinks_and_entire_native_difference():
    from ownpr_central_clock_candidate import candidate
    from audit_native_clock_encoding import audit
    folder=ROOT/'results/vexriscv_central_clock_001';path=folder/'clock_tree.json'
    old=json.loads((ROOT/'results/vexriscv_hard_nets_001/clock_tree_candidate.json').read_text())
    graph=ClockGraph(ROOT/'results/native_clock_vex_input_002');adj=graph.adj
    tree=candidate(graph,old['source'],[p[0] for p in old['paths_sink_to_source']])
    assert tree==json.loads(path.read_text()) and graph.adj is adj
    assert len(tree['paths_sink_to_source'])==1546 and not tree['configuration_verified']
    result=audit(ROOT/'results/vexriscv_central_clock_encoding_001',path,ClockChoices(Device(),graph))
    assert result['status']=='clock_candidate_bits_matched'
    assert result['predicted_changes']==result['actual_changes']==3664
    assert result['required_bits']==17581 and result['edge_kinds']['cmux_switch']==8
    assert result['missing']==result['unexpected']==0 and result['full_payload_restored']


def test_direct_regional_aliases_match_independent_vendor_reference():
    graph=ClockGraph(ROOT/'results/native_clock_vex_input_002');device=Device();choices=ClockChoices(device,graph)
    folder=ROOT/'results/native_vexriscv_clock_reference_001';reference=json.loads((folder/'native_clock_chains.json').read_text())
    pairs={}
    for row in reference['sinks']:
        for dst,src in zip(row['path'],row['path'][1:]):
            dst,src=tuple(dst),tuple(src)
            assert dst not in pairs or pairs[dst]==src
            pairs[dst]=src
    choices.selected_parents=pairs;projection=NativeFrameProjection(device);image=projection.read(folder/'probe.fs');selectors=set();spans=set()
    for dst,src in pairs.items():
        if src[:2] in [(-1,-2),(-2,-1)] and src[2]==245 and dst[2] in [286,292,298,304]:selectors.add(dst)
        elif min(dst[:2])>=1 and choices.primary_alias_parent(src) is not None and pairs.get(src)==src[:2]+(245,):spans.add(dst)
        else:continue
        bits,_=choices.edge(src,dst)
        assert bits and all(int(image[projection.index(p)])-48==v for p,v in bits.items())
    assert len(selectors)==7 and spans
