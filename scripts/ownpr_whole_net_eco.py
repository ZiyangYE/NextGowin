"""Rebuild a critical net's entire tree while freezing all other resources.

Search costs are unloaded estimates. The caller must measure every candidate
with its full parent tree and run complete final STA before accepting it.
"""
import time
from ownpr_router import route, validate


def rebuild(graph, net, critical_sink, owners, deadline, seconds_per_variant=25., root_weights=None, initial_tree=None):
    if critical_sink not in net['sinks']:
        raise ValueError('Critical sink is not part of the original net')
    if net.get('clock') or len(net.get('sources', [net['source']])) != 1 or 'constant_value' in net:
        return [], dict(status='unsupported_clock_or_constant', trials=[])
    blocked = {node for node, owner in owners.items() if owner != net['name']}
    orders = [([critical_sink] + [s for s in net['sinks'] if s != critical_sink], 1.),
              (list(reversed(net['sinks'])), 2.)]
    if root_weights is not None:
        if not 1<=len(root_weights)<=3 or any(not 0<=w<=16 for w in root_weights):raise ValueError('Invalid root arrival weight sweep')
        orders=[([critical_sink]+[s for s in net['sinks'] if s!=critical_sink],float(w)) for w in root_weights]
    previous = getattr(graph, 'root_arrival_weight', None)
    candidates, trials, seen = [], [], set()
    try:
        for sinks, weight in orders:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                trials.append(dict(status='budget_exhausted'))
                break
            graph.root_arrival_weight = weight
            candidate_net = dict(net, sinks=sinks, criticality=1.)
            stamp = time.monotonic()
            trial = dict(sink_order=sinks, root_arrival_weight=weight)
            try:
                result = route(graph, [candidate_net], max_iterations=2,
                    timeout=min(seconds_per_variant, remaining), max_expansions=40000, blocked_nodes=blocked,
                    initial_trees={net['name']:initial_tree} if initial_tree else None)
            except TimeoutError as error:
                trial.update(status='search_timeout', error=str(error))
            else:
                trial['status'] = result['status']
                if result['status'] == 'routed':
                    trial['legality'] = validate(graph, [net], result)
                    if initial_tree:
                        from ownpr_incremental_routes import audit_retention
                        trial['retention']=audit_retention(graph,{net['name']:initial_tree},result)
                    record = result['nets'][net['name']]
                    if set(record['nodes']) & blocked:
                        raise ValueError('Whole-net candidate overlaps frozen resources')
                    key = tuple(sorted(tuple(p) for p in record['pips']))
                    trial.update(pips=len(record['pips']), duplicate=key in seen)
                    if key not in seen:
                        seen.add(key)
                        candidates.append(record)
                else:
                    trial['failed'] = result.get('failed', [])
            trial['seconds'] = time.monotonic() - stamp
            trials.append(trial)
    finally:
        if previous is None:
            del graph.root_arrival_weight
        else:
            graph.root_arrival_weight = previous
    return candidates, dict(status='bounded_whole_net_search', trials=trials,
        candidates=len(candidates), frozen_resources=len(blocked), timing_signoff=False)
