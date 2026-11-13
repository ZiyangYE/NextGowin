"""Exact captured PCLKT1/P33 edge hypotheses, pending reversible native encoding."""
from ownpr_clock_configuration import ClockChoices

class PrimaryP33Hypothesis(ClockChoices):
    def edge(self,source,destination):
        source,destination=tuple(source),tuple(destination)
        a=self.graph.by_coordinate[source];b=self.graph.by_coordinate[destination]
        if b not in self.graph.adj[a]:raise ValueError('Hypothesis edge absent from native topology')
        if (source,destination)==((-2,183,192),(-2,183,213)):
            if self.device.grid[81][181]!=276:raise ValueError('Uncalibrated primary source site')
            table=self.table(276,48)
            if (5,26) in table:raise ValueError('Expected omitted zero-select input')
            masks=[set(v) for (src,dst),v in table.items() if dst==26]
            if not masks or any(m!=masks[0] for m in masks):raise ValueError('Ambiguous primary input mask')
            return {(81,181,y,x):0 for y,x in masks[0]},'hypothesis_primary_zero_select'
        if (source,destination) in (((-2,183,213),(-2,183,216)),((-2,183,216),(-2,-1,132)),((-1,-1,19),(-1,-1,300)),((-1,-1,300),(-2,-1,248))):
            if (source,destination)!=((-2,183,216),(-2,-1,132)) and self.indegree[b]!=1:raise ValueError('Unexpected fixed-prefix fanin')
            return {},'hypothesis_fixed_primary_copy'
        if (source,destination)==((-2,-1,132),(-1,-1,19)):
            choice=self.global_choice(38,132,19)
            if choice is None or choice[:2]!=(54,88):raise ValueError('Uncalibrated central P33 selector')
            r,c,values=choice
            return {(r,c,y,x):v for (y,x),v in values.items()},'hypothesis_central_p33'
        return super().edge(source,destination)
