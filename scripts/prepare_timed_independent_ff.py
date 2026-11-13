"""Propose a hold-filtered batch using complete native setup-path priorities.

This is a scheduling heuristic, not a delay predictor. Every batch still needs
own routing, independent readback, complete native STA and hold acceptance.
"""
import argparse,json,re,time
from pathlib import Path
from ownpr_image_storage import load_json,sha,read_text
from ownpr_device import Device,ROOT
from ownpr_independent_ff_inputs import calibration
from ownpr_independent_ff_candidates import candidates,transform
from ownpr_placement_feedback import load as feedback
from ownpr_ff_configuration import compile_initial
from native_boundary_cec import prove
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--sta',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--limit',type=int,default=4);p.add_argument('--hold-margin',type=float,default=.15);a=p.parse_args()
    if not 1<=a.limit<=16 or not 0<=a.hold_margin<=1:p.error('Invalid bounded batch/hold filter')
    reserve(ROOT,50000000);start=time.monotonic();old=load_json(a.base/'packed.json');placed=load_json(a.base/'placement.json');meta=load_json(a.sta/'manifest.json')
    if meta['packed_sha256']!=sha(a.base/'packed.json') or meta['placement_sha256']!=sha(a.base/'placement.json'):raise ValueError('Timing feedback differs from candidate source')
    weights,info=feedback(a.sta,old,relative_window_ns=2.);text=read_text(a.sta/'probe.tr');mapping=load_json(a.sta/'name_map.json')['instances'];reverse={v:k for k,v in mapping.items()}
    section=re.search(r'^3\.1\.2 Hold Paths Table\n(.*?)(?=\n3\.)',text,re.M|re.S)
    if not section:raise ValueError('Missing complete hold table')
    margins={}
    for slack,alias,pin in re.findall(r'^\s*\d+\s+([-\d.]+)\s+\S+\s+(own_i_\d+)/(\S+)\s+',section[1],re.M):
        if pin=='D':margins[reverse[alias]]=min(margins.get(reverse[alias],float('inf')),float(slack))
    device=Device();cs=candidates(old,placed,device);cells=old['modules']['top']['cells'];ranked=[]
    for c in cs:
        margin=margins.get(c['ff'])
        if margin is None or margin<a.hold_margin:continue
        buffer_priority=max((weights.get((c['buffer'],pin),0.) for pin in cells[c['buffer']]['connections'] if pin.startswith('I')),default=0.)
        moved_priority=max((weights.get((c['lut'],pin),0.) for pin in cells[c['lut']]['connections'] if pin.startswith('I')),default=0.)
        if max(buffer_priority,moved_priority)<=0:continue
        ranked.append((-(2*buffer_priority+moved_priority),c['distance'],c['buffer'],c['lut'],c,margin,buffer_priority,moved_priority))
    ranked.sort();new,layout=old,placed;chosen=[];used=set();rejected=[]
    for _,_,_,_,c,margin,bp,lp in ranked:
        if c['buffer'] in used or c['lut'] in used:continue
        try:
            nd,np,change=transform(new,layout,c,device);np['independent_ff_calibration']=calibration();compile_initial(nd,np)
        except ValueError as e:
            if len(rejected)<20:rejected.append(dict(buffer=c['buffer'],lut=c['lut'],reason=str(e)))
            continue
        new,layout=nd,np;used.update((c['buffer'],c['lut']));chosen.append(dict(change=change,source_hold_margin_ns=margin,buffer_priority=bp,moved_lut_priority=lp))
        if len(chosen)>=a.limit:break
    a.out.mkdir(exist_ok=False)
    if not chosen:
        (a.out/'manifest.json').write_text(json.dumps(dict(status='no_admissible_timed_independent_ff_batch',ranked_candidates=len(ranked),rejected=rejected),indent=2));print('no timed candidates');return
    proof=prove(old,new,a.out/'cec',120);(a.out/'packed.json').write_text(json.dumps(new,indent=2));(a.out/'placement.json').write_text(json.dumps(layout,indent=2))
    result=dict(status='placed',netlist_sha256=sha(a.out/'packed.json'),placement_sha256=sha(a.out/'placement.json'),source_netlist_sha256=sha(a.base/'packed.json'),source_placement_sha256=sha(a.base/'placement.json'),source_sta_sha256=sha(a.sta/'probe.tr'),proof=proof,changes=chosen,ranked_candidates=len(ranked),rejected=rejected,feedback=info,hold_filter_ns=a.hold_margin,clock_endpoints_unchanged=True,ff_locations_unchanged=True,lut_growth=-len(chosen),timing_signoff=False,wall_seconds=time.monotonic()-start,scope='One common batch heuristic: twice buffer setup criticality plus moved-LUT criticality, proximity tie break, complete source hold-margin filter. No predicted or accepted Fmax.')
    (a.out/'manifest.json').write_text(json.dumps(result,indent=2));print('batch',len(chosen),'ranked',len(ranked))


if __name__=='__main__':main()
