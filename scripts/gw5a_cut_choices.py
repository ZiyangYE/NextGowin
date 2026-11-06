"""Bounded equivalent cut inlining with exact live LUT-area recovery.

Architecture candidates are evaluated on the real boundary truth table and
arrival vector. Shared parents stay alive until their last use is removed.
No hard-resource, state, module-port or kept output boundary is crossed.
This is an independent bounded implementation inspired by equivalent-choice
extraction and fanout-aware recovery; native P&R is still required for QoR.
"""
from collections import Counter,deque
import copy
import math
from functools import lru_cache
from gw5a_functional_map import choices,cost,reduce_function


@lru_cache(maxsize=16384)
def patterns(truth,k):return tuple(choices(tuple(range(k)),truth))


def window_truth(nodes,order,inside,boundary,root):
    k=len(boundary);mask=(1<<(1<<k))-1
    values={p:sum(((a>>i)&1)<<a for a in range(1<<k)) for i,p in enumerate(boundary)}
    for p in sorted(inside,key=order.__getitem__):
        n=nodes[p];value=0
        for a in range(1<<len(n['inputs'])):
            if not n['truth']>>a&1:continue
            term=mask
            for i,b in enumerate(n['inputs']):term&=values[b] if a>>i&1 else mask^values[b]
            value|=term
        values[p]=value
    return reduce_function(tuple(boundary),values[root])


def optimize(network,input_arrivals=None,wire_ps=672,lut_ps=521,mux_data_ps=200,mux_select_ps=250,
             max_cuts=16,max_window_nodes=8,area_allowance=0.02):
    n=copy.deepcopy(network);nodes={v['output']:v for v in n['nodes']};order={p:i for i,p in enumerate(n['topological'])}
    refs=Counter(p for v in nodes.values() for p in v['inputs']);refs.update(n['outputs'])
    arrivals={p:float((input_arrivals or {}).get(p,0)) for p in n['inputs']};slots={};constants=set();reports=[]
    def best(ins,truth):
        at={i:arrivals[p]+(0 if p in constants else wire_ps) for i,p in enumerate(ins)}
        return min((cost(p,at,lut_ps,mux_data_ps,mux_select_ps),repr(p),p) for p in patterns(truth,len(ins)))
    for p in n['topological']:
        v=nodes[p];metric,_,plan=best(tuple(v['inputs']),v['truth']);arrivals[p]=metric[0];slots[p]=metric[1]
        if plan[0]=='lut' and not plan[1]:constants.add(p);slots[p]=0
    initial_area=sum(slots.values());area=initial_area;cap=initial_area+math.floor(initial_area*area_allowance)
    def trial(root,old_inputs,new_inputs,new_slots):
        delta=Counter(new_inputs);delta.subtract(old_inputs);removed=set();stack=list(delta)
        while stack:
            p=stack.pop()
            if p in nodes and p not in removed and refs[p]+delta[p]==0:
                removed.add(p)
                for b in nodes[p]['inputs']:delta[b]-=1;stack.append(b)
        return area-slots[root]+new_slots-sum(slots[p] for p in removed),delta,removed
    for root in n['topological']:
        if root not in nodes:continue
        v=nodes[root];metric,_,plan=best(tuple(v['inputs']),v['truth']);arrivals[root]=metric[0]
        # A zero-input logical constant does not consume a standalone LUT.
        original_slots=0 if plan[0]=='lut' and not plan[1] else metric[1]
        area+=original_slots-slots[root];slots[root]=original_slots
        queue=deque([(frozenset([root]),tuple(dict.fromkeys(v['inputs'])))])
        visited=set();candidates=[]
        while queue and len(visited)<max_cuts:
            inside,boundary=queue.popleft()
            if inside in visited:continue
            visited.add(inside)
            if len(inside)>1:
                ins,truth=window_truth(nodes,order,inside,boundary,root)
                score,_,candidate=best(ins,truth)
                candidate_slots=0 if candidate[0]=='lut' and not candidate[1] else score[1]
                new_area,delta,removed=trial(root,v['inputs'],ins,candidate_slots)
                if score[0]<metric[0]-1e-6 and new_area<=cap:
                    candidates.append((score[0],new_area,repr(ins),ins,truth,candidate_slots,delta,removed))
            if len(inside)>=max_window_nodes:continue
            # Expand the latest available boundary first, but retain siblings.
            for p in sorted((p for p in boundary if p in nodes),key=lambda p:-arrivals[p]):
                expanded=inside|{p}
                leaves=tuple(dict.fromkeys(b for b in list(boundary)+nodes[p]['inputs'] if b not in expanded))
                if len(leaves)<=6 and len(expanded)<=max_window_nodes:queue.append((expanded,leaves))
        if not candidates:continue
        delay,new_area,_,ins,truth,new_slots,delta,removed=min(candidates,key=lambda r:r[:3])
        previous=arrivals[root];v.update(inputs=list(ins),truth=truth)
        for p,d in delta.items():refs[p]+=d
        for p in removed:del nodes[p]
        slots[root]=new_slots;area=new_area;arrivals[root]=delay
        if not ins:constants.add(root)
        reports.append(dict(output=root,old_arrival_ps=previous,new_arrival_ps=delay,removed_nodes=sorted(removed),
                            final_live_lut_slots=area,truth_variables=len(ins),proof_cases=1<<len(ins)))
    if area>cap:
        return copy.deepcopy(network),dict(schema='bounded-cut-choice-v1',rejected='Final fastest-cover area exceeded the fixed budget',rewrites=[],initial_lut_slots=initial_area,final_lut_slots=initial_area,cap=cap)
    n['nodes']=[nodes[p] for p in n['topological'] if p in nodes];n['topological']=[v['output'] for v in n['nodes']]
    return n,dict(schema='bounded-cut-choice-v1',max_cuts=max_cuts,max_window_nodes=max_window_nodes,
                  initial_lut_slots=initial_area,final_lut_slots=area,cap=cap,rewrites=reports,
                  verification='Window truth computed from the actual graph; whole-network CEC required before use',
                  timing_scope='Analytical costs; native placement/routing and hold checks required')
