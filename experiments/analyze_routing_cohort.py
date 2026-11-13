"""Inspect measured reported paths; do not infer unreported internal paths."""
import json
from pathlib import Path
import statistics
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from routing_path_diagnosis import diagnose
from run_abc_reproduction import sha

def analyze(path):
    paths=diagnose(path.read_text())
    # A shared net edge may occur in several reported paths. Count it once here.
    edges={}
    for p in paths:
        for e in p['edges']:edges[(e['source'],e['destination'])]=e
    fabric=[e for e in edges.values() if e['manhattan_tiles'] is not None]
    return dict(report=str(path.relative_to(ROOT)),sha256=sha(path),reported_path_count=len(paths),
        worst_path=paths[0],unique_reported_edges=len(edges),
        fabric_edge_statistics=dict(count=len(fabric),
            over_1ns=sum(e['delay_ns']>1 for e in fabric),
            over_1ns_fanout_at_most_4=sum(e['delay_ns']>1 and e['source_fanout']<=4 for e in fabric),
            median_delay_ns=statistics.median(e['delay_ns'] for e in fabric) if fabric else None),
        longest_reported_edges=sorted(edges.values(),key=lambda e:-e['delay_ns'])[:20],paths=paths)

def main():
    result=dict(scope='Only reported top setup paths; not a whole-chip distribution. Tile distance is endpoint Manhattan span, not routed length.',designs={})
    for name in ['ctrl','priority','int2float','cavlc','dec','router','i2c','sin','bar','multiplier']:
        result['designs'][name]={}
        for side,p in [('vendor',ROOT/'results/general_synthesis_suite_012'/name/'vendor_pnr/mapped.tr'),('own',ROOT/'results/fanout_development_suite_001'/name/'own_pnr/mapped.tr')]:
            result['designs'][name][side]=analyze(p)
    for side,p in [('vendor',ROOT/'results/fpgafly_fresh_vendor_001/pnr/mapped.tr'),('own',ROOT/'results/fpgafly_fanout_cold_002/flow/06_pnr/mapped.tr')]:
        if p.exists():result.setdefault('fpgafly',{})[side]=analyze(p)
    out=ROOT/'results/routing_cohort_diagnosis_001.json';out.write_text(json.dumps(result,indent=2)+'\n')
    for name, sides in result['designs'].items():
        for side,r in sides.items():
            p=r['worst_path'];print(name,side,'wire/cell',round(p['data_wire_ns'],3),round(p['data_cell_ns'],3),'wire edges',p['wire_edges'],'top3',round(p['top_three_wire_fraction'],3),'stats',r['fabric_edge_statistics'])

if __name__=='__main__':main()
