"""Bounded, fixed-state placement proposals scored by competing timing paths.

Native baseline arcs anchor a distance-delta surrogate. This is not a routed
delay prediction: all selected changes require fresh routing and complete STA.
Unlike wirelength relaxation, the objective is worst endpoint setup slack.
"""
import argparse,copy,json,math,shutil,time
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,sha
from ownpr_clock_slack import compile_model
from ownpr_arrival_graph import propagate
from ownpr_global_relayout import units
from ownpr_critical_chain_move import exclusive_tiles
from workspace_budget import reserve


def search(design,placed,feedback,device,seconds=120,radius=4):
    start=time.monotonic();deadline=start+seconds
    graph=feedback['graph'];initial_values=propagate(**graph)[0]
    from ownpr_arrival_evaluator import ArrivalEvaluator
    from ownpr_bidir_timing import analyze
    evaluator=ArrivalEvaluator(graph);evaluated=evaluator.evaluate()
    for n,v in initial_values.items():
        if evaluated[evaluator.ids[n]:evaluator.ids[n]+2]!=v:raise ValueError('Compiled arrival propagation differs')
    groups,owner=units(design,placed)
    points={n:tuple(groups[u]['point']) for n,u in owner.items()}
    initial=dict(points);cells=design['modules']['top']['cells'];excluded=exclusive_tiles(placed)
    movable=sorted(n for n,r in placed['clusters'].items() if not r.get('ff') and r.get('lut')==n
        and cells[n]['type'] in ('LUT1','LUT2','LUT3','LUT4') and (r['row'],r['column']) not in excluded)
    terminals={}
    for row in feedback['setup']:
        n,tr=row['endpoint'],row['transition']
        terminals.setdefault(n,[1e10,1e10])[tr]=initial_values[n][tr]+row['reported_slack_ns']
    slack=analyze(graph,terminals)['slack']
    movable.sort(key=lambda n:(min(slack.get(n+'/F',[math.inf])),n))
    movable_set=set(movable)
    occupied={(r['row'],r['column'],r['slot']):n for n,r in placed['clusters'].items()}
    base_sites={n:(r['row'],r['column'],r['slot']) for n,r in placed['clusters'].items()}
    sites=dict(base_sites);wires=[]
    for i,e in enumerate(graph['edges']):
        a=e['source'].rsplit('/',1)[0];b=e['destination'].rsplit('/',1)[0]
        if e['kind']!='wire' or a not in points or b not in points or not ({a,b}&movable_set):continue
        dist=sum(abs(x-y) for x,y in zip(initial[a],initial[b]))
        # Bounded empirical geometry sensitivity; never an acceptance bound.
        slope=min(.07,max(.025,(max(e['delay'])-.15)/(dist+1)))
        wires.append((i,a,b,dist,slope))
    labels=feedback['setup'];evaluations=0
    def score():
        nonlocal evaluations
        overrides={i:[max(0.,v+slope*(sum(abs(x-y) for x,y in zip(points[a],points[b]))-old)) for v in graph['edges'][i]['delay']] for i,a,b,old,slope in wires}
        values=evaluator.evaluate(overrides);slacks=[]
        for row in labels:
            n,tr=row['endpoint'],row['transition'];delta=values[evaluator.ids[n]+tr]-initial_values[n][tr]
            if not math.isfinite(delta):raise ValueError('Lost modeled endpoint')
            slacks.append(row['reported_slack_ns']-delta)
        evaluations+=1
        return (round(min(slacks),6),round(sum(min(s,0.) for s in slacks),6),round(sum(min(s,1.) for s in slacks),6))
    best=score();baseline=best;moves=[];passes=0
    while time.monotonic()<deadline:
        improved=False;passes+=1
        for name in movable:
            if time.monotonic()>=deadline:break
            old=sites[name];r,c,slot=old;br,bc,_=base_sites[name]
            candidates=sorted({(r+dy,c+dx,slot) for step in (1,2) for dy,dx in ((step,0),(-step,0),(0,step),(0,-step))})
            selected=None;selected_score=best
            for y,x,s in candidates:
                if time.monotonic()>=deadline:break
                if not (0<=y<device.rows and 0<=x<device.cols) or device.grid[y][x]!=17 or (y,x) in excluded:continue
                if abs(y-br)+abs(x-bc)>radius:continue
                other=occupied.get((y,x,s))
                if other and (other not in movable_set or abs(r-base_sites[other][0])+abs(c-base_sites[other][1])>radius):continue
                points[name]=(y,x)
                if other:points[other]=(r,c)
                proposed=score()
                points[name]=(r,c)
                if other:points[other]=(y,x)
                if proposed>selected_score:selected=(y,x,s,other);selected_score=proposed
            if selected:
                y,x,s,other=selected;occupied.pop(old);occupied[(y,x,s)]=name;sites[name]=(y,x,s);points[name]=(y,x)
                if other:occupied[old]=other;sites[other]=old;points[other]=(r,c)
                moves.append(dict(instance=name,old=list(old),new=[y,x,s],swapped_with=other,before=best,after=selected_score))
                best=selected_score;improved=True
        if not improved:break
    changed=copy.deepcopy(placed)
    for name,site in sites.items():changed['clusters'][name].update(zip(('row','column','slot'),site))
    from ownpr_mux_macros import validate as validate_mux
    from ownpr_carry_macros import validate as validate_carry
    from ownpr_ff_configuration import compile_initial
    validate_mux(design,changed,device);validate_carry(design,changed,device);compile_initial(design,changed)
    if placed.get('ioreg_clusters'):
        from ownpr_ioreg_macros import validate
        validate(design,changed,device)
    assert changed['fixed_io']==placed['fixed_io']
    assert {n:r for n,r in changed['clusters'].items() if r.get('ff')}=={n:r for n,r in placed['clusters'].items() if r.get('ff')}
    changed.update(timing_signoff=False,physical_legality_complete=False)
    return changed,dict(baseline_score=baseline,selected_score=best,evaluations=evaluations,passes=passes,moves=moves,
        eligible_luts=len(movable),timing_edges=len(graph['edges']),timing_nodes=len(graph['nodes']),reported_endpoint_transitions=len(labels),
        competing_paths_repropagated=True,all_native_transitions_modeled=False,ordering='propagated_setup_slack',compiled_arrival_baseline_exact=True,radius=radius,wall_seconds=time.monotonic()-start,
        ranking_only=True,hold_predicted=False,clock_and_state_placement_fixed=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('base','sta','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--seconds',type=int,default=120);p.add_argument('--radius',type=int,default=4);a=p.parse_args()
    if not 1<=a.seconds<=300 or not 1<=a.radius<=8:p.error('Invalid bounded proposal search')
    reserve(ROOT,20000000);design=load_json(a.base/'packed.json');placed=load_json(a.base/'placement.json')
    feedback=compile_model(a.base,a.sta)
    changed,report=search(design,placed,feedback,Device(),a.seconds,a.radius)
    out=a.out.resolve();out.mkdir(exist_ok=False);shutil.copyfile(a.base/'packed.json',out/'packed.json')
    (out/'placement.json').write_text(json.dumps(changed,indent=2));(out/'search.json').write_text(json.dumps(report,indent=2))
    shutil.copyfile(__file__,out/'operator_source.py.txt')
    shutil.copyfile(ROOT/'scripts/ownpr_arrival_evaluator.py',out/'arrival_evaluator_source.py.txt')
    meta=dict(status='placed',netlist_sha256=sha(out/'packed.json'),placement_sha256=sha(out/'placement.json'),
        source_placement=str(a.base.resolve()),source_placement_sha256=sha(a.base/'placement.json'),source_sta=str(a.sta.resolve()),
        source_sha256=feedback['source_sha256'],operator_sha256=sha(out/'operator_source.py.txt'),
        logical_netlist_byte_identical=True,interface_cycles_changed=False,timing_signoff=False,physical_legality_complete=False,search=report)
    (out/'manifest.json').write_text(json.dumps(meta,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='moves'},indent=2))


if __name__=='__main__':main()
