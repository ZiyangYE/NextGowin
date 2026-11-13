"""Whole-tree clock coordinate descent scored by setup/hold feedback."""
import time
from ownpr_clock_configuration import ClockChoices
from ownpr_clock_slack import clock_rows,score
from ownpr_clock_timing_search import make_tree,measure


def reconnect_group(paths,candidate):
    """Replace a shared clock prefix for every affected sink, not just one leaf."""
    candidate=tuple(candidate)
    if len(set(candidate))!=len(candidate):raise ValueError('Cyclic clock proposal')
    indices={n:i for i,n in enumerate(candidate)};changed={}
    for target,path in paths.items():
        if path[0]!=candidate[0]:raise ValueError('Clock source changed')
        joins=[i for i,n in enumerate(path) if n in indices];j=joins[-1]
        next_path=candidate[:indices[path[j]]+1]+path[j+1:]
        if next_path[-1]!=target or len(next_path)!=len(set(next_path)):raise ValueError('Clock prefix created a loop or changed a sink')
        changed[target]=next_path
    return changed


def identity(paths):
    parents={}
    for path in paths.values():
        for i,n in enumerate(path):
            p=path[i-1] if i else None
            if n in parents and parents[n]!=p:raise ValueError('Conflicting clock parents after prefix replacement')
            parents[n]=p
    return tuple(sorted(parents.items()))


def ordered_proposals(costs,graph,priorities):
    targets=sorted(costs,key=lambda t:(priorities.get(tuple(graph.nodes[t]['coordinate']),float('inf')),t))
    for rank in range(max(map(len,costs.values()))):
        for target in targets:
            if rank<len(costs[target]):yield target,costs[target][rank]


def optimize(native,graph,device,baseline,costs,pointers,base_bits,model,deadline,max_trials=256,max_passes=4):
    current={graph.by_coordinate[tuple(p[0])]:tuple(graph.by_coordinate[tuple(n)] for n in reversed(p)) for p in baseline['paths_sink_to_source']}
    if set(current)!=set(costs):raise ValueError('Clock search changed targets')
    codec=ClockChoices(device,graph);source=baseline['source']
    def query(paths):
        tree=make_tree(graph,source,list(paths.values()));bits,check=codec.requirements(tree)
        if check['conflicts'] or any(b in base_bits and base_bits[b]!=v for b,v in bits.items()):raise ValueError('Clock/data configuration conflict')
        rows,nodes=measure(native,graph,list(paths.values()),pointers)
        return rows,nodes,model.evaluate(rows)
    rows,nodes,best=query(current);actual=clock_rows(rows)
    error=max(abs(actual[n][s][i]-v[s][i]) for n,v in model.base.items() for s in range(3) for i in range(4))
    if error>2e-6:raise ValueError('Baseline clock query differs from independently captured final STA')
    if score(best) is None:raise ValueError('Baseline violates conservative clock feedback guards')
    initial=best;seen={identity(current)};trials=[];accepted=[];reason='local_candidates_exhausted'
    for iteration in range(max_passes):
        winner=None;winner_metrics=best
        for target,option in ordered_proposals(costs,graph,model.priorities()):
            if time.monotonic()>=deadline or len(trials)>=max_trials:reason='search_budget_reached';break
            try:proposed=reconnect_group(current,option[3]);key=identity(proposed)
            except ValueError:continue
            if key in seen:continue
            seen.add(key);record=dict(pass_number=iteration+1,proposal_sink=graph.nodes[target]['coordinate'],proposal_path_ids=list(option[3]),changed_sinks=sum(current[n]!=p for n,p in proposed.items()))
            try:
                measured,values,metrics=query(proposed);record.update(metrics=metrics,status='eligible' if score(metrics) is not None else 'conservative_guard_rejected')
                if score(metrics) is not None and score(metrics)>score(winner_metrics):winner=(proposed,measured,values);winner_metrics=metrics
            except ValueError as e:record.update(status='rejected',reason=str(e))
            trials.append(record)
        if winner is None:break
        current,rows,nodes=winner;best=winner_metrics;accepted.append(dict(pass_number=iteration+1,metrics=best))
        if reason=='search_budget_reached':break
    else:reason='pass_budget_reached'
    repeated,repeated_nodes=measure(native,graph,list(current.values()),pointers)
    if repeated!=rows or repeated_nodes!=nodes:raise ValueError('Selected slack-driven clock tree is not repeatable')
    return list(current.values()),rows,nodes,dict(status=reason,initial_metrics=initial,selected_metrics=best,
        baseline_clock_query_error_ns=error,trials=trials,accepted=accepted,max_trials=max_trials,max_passes=max_passes,
        proposal_order='Worst reported setup slack, round-robin alternative ranks across all sinks',
        timing_signoff=False,scope='Grouped shared-prefix coordinate descent, full merged-tree queries and competing setup arrivals; local search, not global optimality or final STA.')
