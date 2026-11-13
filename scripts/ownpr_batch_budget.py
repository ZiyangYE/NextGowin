"""Conservative additive screening of measured fixed-placement wire changes.

Charges every other net's worst positive loss to every changed receiver.
No independence assumption or cancellation of gains is used. This screening
is not a full STA proof; clocks, cell-mode effects and final coverage still
require the independent complete export audit.
"""
import math


def consumption(name,before,after,budgets):
    if not before or set(before)!=set(after) or set(before)!=set(budgets):
        raise ValueError('Incomplete batch receiver set')
    rows=[]
    for sink in before:
        a,b=before[sink],after[sink];limit=budgets[sink]
        if len(a)!=4 or len(b)!=4 or not all(math.isfinite(v) for v in [*a,*b,limit['setup'],limit['hold']]):
            raise ValueError('Invalid measured batch timing')
        if min(limit['setup'],limit['hold'])<0:raise ValueError('Negative batch budget')
        rows.append(dict(sink=sink,setup_loss=max(0.,b[0]-a[0],b[1]-a[1]),hold_loss=max(0.,a[2]-b[2],a[3]-b[3]),setup_budget=limit['setup'],hold_budget=limit['hold']))
    return dict(net=name,receivers=rows,setup=max(r['setup_loss'] for r in rows),hold=max(r['hold_loss'] for r in rows))


def check(records):
    if len({r['net'] for r in records})!=len(records):raise ValueError('Duplicate net in batch')
    total={k:sum(r[k] for r in records) for k in ('setup','hold')};violations=[]
    for record in records:
        for row in record['receivers']:
            for kind in ('setup','hold'):
                cumulative=row[kind+'_loss']+total[kind]-record[kind]
                if cumulative>row[kind+'_budget']+1e-9:
                    violations.append(dict(net=record['net'],sink=row['sink'],kind=kind,cumulative_loss_ns=cumulative,budget_ns=row[kind+'_budget']))
    return dict(compatible=not violations,total_worst_positive_losses_ns=total,violations=violations,
                scope='All-to-all additive wire-delta screening; full STA mandatory',full_sta_proven=False)
