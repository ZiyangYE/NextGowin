"""Exact LUT4 pin choices using whole-graph min/max mapping feedback.

This is an unplaced search. It never certifies hold closure, and never changes
the final SDC. Existing identity LUT1 adapters are deliberately retained.
"""
import copy,itertools
from ownpr_state_safety import protected_cells


def permute(cell,order):
    if cell['type']!='LUT4' or sorted(order)!=list(range(4)):raise ValueError('Expected LUT4 bijection')
    result=copy.deepcopy(cell);truth=int(cell['parameters']['INIT'],2);word=0
    for address in range(16):
        old=sum(((address>>new)&1)<<pin for new,pin in enumerate(order));word|=((truth>>old)&1)<<address
    result['parameters']['INIT']=format(word,'016b')
    for new,old in enumerate(order):result['connections']['I'+str(new)]=list(cell['connections']['I'+str(old)])
    return result


def improve(design,engine,limit=12):
    new=copy.deepcopy(design);_,metrics,_=engine.evaluate(new);initial=copy.deepcopy(metrics);protected=protected_cells(design);changes=[]
    def quality(m):
        h,s=m['hold'],m['setup'];return (h['wns'],-h['violations'],h['tns'],s['wns'],s['tns'])
    cells=new['modules']['top']['cells'];roots=sorted((n for n,c in cells.items() if c['type']=='LUT4' and n not in protected and n+'/F' in metrics['hold']['slack'] and min(metrics['hold']['slack'][n+'/F'])<0),key=lambda n:(min(metrics['hold']['slack'][n+'/F']),n))[:limit]
    trials=0
    for n in roots:
        original=copy.deepcopy(cells[n]);best=metrics;selected=None
        for order in itertools.permutations(range(4)):
            if order==(0,1,2,3):continue
            cells[n]=permute(original,order);_,candidate,_=engine.evaluate(new);trials+=1
            if candidate['setup']['wns']+1e-8<metrics['setup']['wns']:continue
            if quality(candidate)>quality(best):best=candidate;selected=order
        cells[n]=original if selected is None else permute(original,selected)
        if selected is not None:
            changes.append(dict(cell=n,new_to_old_inputs=list(selected),previous_hold_wns_ns=metrics['hold']['wns'],new_hold_wns_ns=best['hold']['wns'],previous_hold_tns_ns=metrics['hold']['tns'],new_hold_tns_ns=best['hold']['tns']));metrics=best
    summary=lambda x:{mode:{k:r[k] for k in ('wns','tns','violations')} for mode,r in x.items()}
    return new,dict(scope=__doc__,changes=changes,permutations_evaluated=trials,before=summary(initial),after=summary(metrics),native_timing_verified=False)
