"""Cross-check table values against report arcs; physical pin mapping is separate."""
import argparse
import json
from pathlib import Path
from extract_timing import extract


def validate(model, sources):
    rows = {(r['group'],r['row']):r['values'] for r in model['runtime_rows']}
    checks = []
    for source in sources:
        meta = source['metadata']
        if meta['Part Number'] != model['part']:
            raise ValueError('Report part mismatch')
        if meta['Setup Delay Model'] != model['corners']['setup'] or meta['Hold Delay Model'] != model['corners']['hold']:
            raise ValueError('Report corner mismatch')
        for arc in source['arcs']:
            if arc['kind']=='tINS' and arc['cell'] in ('LUT1','LUT2','LUT3','LUT4'):
                candidates = [rows[0,r] for r in range(8)]
                kind = 'LUT value set'
            elif arc['kind']=='tC2Q' and arc['cell']=='DFFRE':
                candidates = [rows[model['ff_clock_to_q']['group'],24]]
                kind = 'DFFRE CLK to Q'
            elif (model.get('ff_data_constraints') and arc['cell']=='DFFRE' and
                  arc.get('constrained_pin')=='D' and arc['kind'] in ('tSu','tH','tHld')):
                ff=model['ff_data_constraints']
                entry=ff['setup'] if arc['kind']=='tSu' else ff['hold']
                ids=entry['runtime_rows'] if 'runtime_rows' in entry else [entry['runtime_row']]
                candidates=[[entry['report_adjustment_sign']*v for v in rows[ff['group'],r]] for r in ids]
                kind='DFFRE D setup' if arc['kind']=='tSu' else 'DFFRE D hold'
            else:
                continue
            slots = (6,7) if arc['analysis'] in ('setup','recovery') else (8,9)
            delta = min(abs(arc['delay_ns']-values[i]) for values in candidates for i in slots)
            checks.append(dict(kind=kind,report=source['report'],line=arc['line'],instance=arc['instance'],
                analysis=arc['analysis'],observed_ns=arc['delay_ns'],nearest_error_ns=delta,matched=delta<=.000501))
    if not checks:
        raise ValueError('No comparable arcs')
    return dict(scope='LUT and FF data-constraint checks match candidate value sets; no logical-to-physical pin or per-instance FF routing-mode assignment',
        checked=len(checks),matched=sum(c['matched'] for c in checks),all_matched=all(c['matched'] for c in checks),checks=checks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model',type=Path,required=True)
    ap.add_argument('--pair',nargs=2,action='append',required=True)
    ap.add_argument('--out',type=Path,required=True)
    args = ap.parse_args()
    result = validate(json.loads(args.model.read_text()),[extract(Path(a),Path(b)) for a,b in args.pair])
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(f'{result["matched"]}/{result["checked"]} observed arcs match within report precision')
    if not result['all_matched']:
        raise SystemExit(1)


if __name__=='__main__':
    main()
