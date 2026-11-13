"""Compile a fixed arrival DAG once for repeated placement proposal scoring."""
import math
from ownpr_bidir_timing import topology


class ArrivalEvaluator:
    def __init__(self,graph):
        order,incoming,_=topology(graph);self.ids={n:2*i for i,n in enumerate(order)}
        self.seed=[-math.inf]*(2*len(order));self.edges=[]
        for n,v in graph['seeds'].items():
            if n not in self.ids or len(v)!=2 or any(not math.isfinite(x) for x in v):raise ValueError('Invalid arrival seed')
            i=self.ids[n];self.seed[i:i+2]=v
        for n in order:
            for k in incoming[n]:
                e=graph['edges'][k];a=self.ids[e['source']];b=self.ids[e['destination']]
                self.edges.append((k,e['delay'],[(a+s,b+d,d) for s,d in e['transitions']]))
        self.count=len(graph['edges'])

    def evaluate(self,overrides=None):
        overrides=overrides or {}
        if any(type(i)!=int or not 0<=i<self.count or len(v)!=2 or any(not math.isfinite(x) or x<0 for x in v) for i,v in overrides.items()):
            raise ValueError('Invalid arrival delay override')
        values=self.seed.copy()
        for k,delay,transitions in self.edges:
            ds=overrides.get(k,delay)
            for a,b,t in transitions:
                candidate=values[a]+ds[t]
                if candidate>values[b]:values[b]=candidate
        return values
