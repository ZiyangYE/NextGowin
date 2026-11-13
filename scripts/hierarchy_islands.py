"""Keep stateful RTL islands as an optional physical heuristic.

No design-name rules: count native DSPs or semantically checked multiply helpers
and direct flip-flop instances. A zero multiplier threshold keeps all modules
with sufficient direct state. This changes optimization scope, not RTL state.
"""
import re
from gowin_cones import masked
from verific_bridge import units


def detect(text,verified_multipliers,min_state=16,min_multipliers=2):
    kinds=set(verified_multipliers)|{'MULT12X12','MULTALU27X18','MULT27X36','MULTADDALU12X12'}
    multiply=re.compile(r'(?m)^\s*(?:'+'|'.join(re.escape(k) for k in sorted(kinds))+r')\s+')
    state=re.compile(r'(?m)^\s*(?:VERIFIC_DFF\w*|DFFRE|DFFSE|DFFCE|DFFPE)\s+')
    rows=[]
    for name,(a,b) in units(text).items():
        body=masked(text[a:b]);m=len(multiply.findall(body));f=len(state.findall(body))
        if m>=min_multipliers and f>=min_state:rows.append(dict(module=name,multiply_instances=m,direct_state_instances=f))
    return rows
