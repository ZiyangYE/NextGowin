"""Calibrate a small nonnegative routing-cost heuristic from reported paths.

This is a placement-snapshot model, not pre-route STA or a signoff bound.
Training and validation reports are explicitly separate.
"""
import argparse
import itertools
import json
import math
from pathlib import Path
import re
import numpy as np
from extract_timing import ROW
from path_accounting import section
from run_abc_reproduction import sha


def position(value):
    match=re.fullmatch(r'R(\d+)C(\d+)\[\d+\]\[[A-Z]\]',value)
    return [int(match[1]),int(match[2])] if match else None


def samples(text):
    body=section(text,'3.3.1 Setup Analysis Report')
    if not body:raise ValueError('Missing main setup analysis report')
    chunks=re.split(r'^\s*Path(\d+)\s*$',body,flags=re.M);found={}
    for i in range(1,len(chunks),2):
        chunk=chunks[i+1]
        slack=re.search(r'^Slack[ \t]*:[ \t]*([-+\d.]+)',chunk,re.M)
        if not slack or 'Data Arrival Path:' not in chunk:continue
        arrival=chunk.split('Data Arrival Path:',1)[1].split('Data Required Path:',1)[0]
        rows=[m.groups() for line in arrival.splitlines() if (m:=ROW.match(line))]
        data=False;previous=None
        for row in rows:
            if row[2] in ('tC2Q','tIn'):data=True
            if data and row[2]=='tNET' and previous and previous[2] in ('tC2Q','tINS'):
                source,target=position(previous[5]),position(row[5])
                if source and target:
                    src,_,src_pin=previous[6].rpartition('/');dst,_,dst_pin=row[6].rpartition('/')
                    item=dict(source=src,source_pin=src_pin,sink=dst,sink_pin=dst_pin,source_position=source,
                        sink_position=target,fanout=int(previous[4]),delay_ns=float(row[1]),path=int(chunks[i]),slack_ns=float(slack[1]))
                    key=(src,src_pin,dst,dst_pin)
                    if key not in found or item['delay_ns']>found[key]['delay_ns']:found[key]=item
            previous=row
    return list(found.values())


def features(row):
    source,target=row['source_position'],row['sink_position']
    return [1,abs(source[0]-target[0]),abs(source[1]-target[1]),math.log2(max(1,row['fanout']))]


def fit(rows):
    if len(rows)<4:raise ValueError('Too few physical wire samples')
    x=np.asarray([features(r) for r in rows],dtype=float);y=np.asarray([r['delay_ns'] for r in rows])
    # Four features permit exact active-set enumeration of nonnegative LS.
    best=np.zeros(4);loss=float(y@y)
    for count in range(1,5):
        for active in itertools.combinations(range(4),count):
            beta=np.linalg.lstsq(x[:,active],y,rcond=None)[0]
            if min(beta)<-1e-10:continue
            candidate=np.zeros(4);candidate[list(active)]=np.maximum(beta,0)
            current=float(np.sum((x@candidate-y)**2))
            if current<loss:best,loss=candidate,current
    return dict(features=['bias','row_distance','column_distance','log2_driver_fanout'],coefficients=best.tolist(),floor_ns=.05,
        scope='Nonnegative least-squares fit to selected reported setup wires at known placement coordinates; no signoff or out-of-sample upper-bound guarantee')


def predict(model,row):return max(model['floor_ns'],sum(a*b for a,b in zip(model['coefficients'],features(row))))


def errors(model,rows):
    residual=np.asarray([predict(model,r)-r['delay_ns'] for r in rows])
    if not len(residual):raise ValueError('No comparable validation wires')
    return dict(samples=len(rows),mae_ns=float(np.mean(abs(residual))),rmse_ns=float(np.sqrt(np.mean(residual**2))),
        maximum_underestimate_ns=max(0,float(-min(residual))),maximum_overestimate_ns=max(0,float(max(residual))))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--train',type=Path,action='append',required=True)
    ap.add_argument('--validate',type=Path,action='append',required=True);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    if {sha(p) for p in args.train}&{sha(p) for p in args.validate}:raise ValueError('Training and validation reports overlap')
    train=[];validation=[];metadata=[]
    for paths,collection in [(args.train,train),(args.validate,validation)]:
        for path in paths:
            text=path.read_text(errors='replace')
            header=dict(re.findall(r'^<([^>]+)>:[ \t]*(.*?)[ \t]*$',text,re.M))
            metadata.append((header.get('Part Number'),header.get('Setup Delay Model')))
            collection.extend(dict(r,report_sha256=sha(path)) for r in samples(text))
    if len(set(metadata))!=1 or None in metadata[0]:raise ValueError('Wire model reports have different/unidentified device/corner')
    model=fit(train)
    result=dict(model=model,train_reports={str(p):sha(p) for p in args.train},validation_reports={str(p):sha(p) for p in args.validate},
        device_corner=metadata[0],numpy_version=np.__version__,training_errors=errors(model,train),validation_errors=errors(model,validation),
        validation_scope='Held-out placement/report; not an independent circuit suite',training_samples=train,validation_samples=validation)
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('training_samples','validation_samples')},indent=2))


if __name__=='__main__':main()
