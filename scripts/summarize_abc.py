"""Summarize all failures and paired valid results from a reproduction manifest."""
import argparse
import csv
import json
import math
from pathlib import Path


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('manifest',type=Path)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    data=json.loads(args.manifest.read_text())
    summary=dict(source=str(args.manifest),source_status=data['status'],lut_size=data['lut_size'],
        cases=[],paired={})
    ratios={}
    for item in data['runs']:
        case=dict(benchmark=item['benchmark'],status=item.get('status','in_progress'),configs={},
            preprocess_status=item.get('preprocess',{}).get('status'),
            preprocess_cec_status=item.get('preprocess_cec',{}).get('status'))
        base=item['configs'].get('baseline',{})
        for label,config in item['configs'].items():
            case['configs'][label]=dict(valid=config.get('valid',False),samples=len(config['samples']),
                summary=config.get('summary'),failures=[s['status'] for s in config['samples'] if s['status']!='ok'])
            if label!='baseline' and base.get('valid') and config.get('valid'):
                row=dict(benchmark=item['benchmark'])
                for metric in ['luts','edges','depth']:
                    row[metric]=config['summary'][metric]/base['summary'][metric]
                ratios.setdefault(label,[]).append(row)
        summary['cases'].append(case)
    for label,rows in ratios.items():
        summary['paired'][label]=dict(count=len(rows),benchmarks=[r['benchmark'] for r in rows],
            geometric_mean_ratio={metric:math.exp(sum(math.log(r[metric]) for r in rows)/len(rows))
                for metric in ['luts','edges','depth']})
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(summary,indent=2)+'\n')
    with args.out.with_suffix('.csv').open('w',newline='',encoding='utf8') as stream:
        columns=['benchmark','case_status','configuration','valid','luts','edges','depth',
                 'mapping_cpu_seconds','wall_seconds','failure_status']
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        for case in summary['cases']:
            if not case['configs']:
                writer.writerow(dict(benchmark=case['benchmark'],case_status=case['status'],
                    valid=False,failure_status=case['preprocess_cec_status'] or case['preprocess_status']))
            for label,config in case['configs'].items():
                writer.writerow(dict(benchmark=case['benchmark'],case_status=case['status'],
                    configuration=label,valid=config['valid'],**(config['summary'] or {}),
                    failure_status=';'.join(config['failures'])))
    print(json.dumps({k:v for k,v in summary.items() if k!='cases'},indent=2))


if __name__=='__main__':
    main()
