"""Allow exact-clone source substitution on fixed wide-LUT external inputs.

Wide leaf positions, functions, output bits and dedicated links are immutable.
Only a wire driven by a structurally identical ordinary LUT clone may replace
an input. Actual graph rebuilding uses the real candidate, never this audit
view. Global CEC and native STA remain separate mandatory gates.
"""
import copy
from ownpr_carry_feedback import FixedBoundaryCarryFeedback
from ownpr_lut_graph_feedback import LUTS
from ownpr_shared_dag import connectivity
from ownpr_state_safety import protected_cells


def normalize_clone_inputs(reference,candidate,base_layout,layout,private):
    old=reference['modules']['top']['cells'];new=candidate['modules']['top']['cells'];old_drivers,_=connectivity(reference);new_drivers,_=connectivity(candidate);bad=protected_cells(reference);audit=copy.deepcopy(candidate);changed=[]
    def shape(cell):
        c=copy.deepcopy(cell);c['connections']['F']=['<output>'];return c
    def exact_clone(source,clone,visiting=None):
        # Accept a structurally identical LUT DAG, not just identical raw bit
        # numbers. Nested selected copies may legitimately feed each other.
        if source not in old or clone not in new or old[source]['type'] not in LUTS or new[clone]['type'] not in LUTS:return False
        a,b=old[source],new[clone];visiting=set() if visiting is None else visiting;key=source,clone
        if key in visiting:return False
        if {k:v for k,v in a.items() if k!='connections'}!={k:v for k,v in b.items() if k!='connections'} or set(a['connections'])!=set(b['connections']):return False
        visiting=visiting|{key}
        for p,x in a['connections'].items():
            if p=='F':continue
            y=b['connections'][p]
            if x==y:continue
            if len(x)!=1 or len(y)!=1 or not exact_clone(old_drivers.get(x[0]),new_drivers.get(y[0]),visiting):return False
        return True
    for n in private:
        if new.get(n)==old[n]:continue
        if n in bad or n not in new or layout['clusters'].get(n)!=base_layout['clusters'][n]:raise ValueError('Moved/protected wide leaf input substitution')
        a,b=old[n],new[n]
        if a['type']!='LUT4' or {k:v for k,v in a.items() if k!='connections'}!={k:v for k,v in b.items() if k!='connections'} or set(a['connections'])!=set(b['connections']) or a['connections']['F']!=b['connections']['F']:raise ValueError('Changed fixed wide leaf function/output/metadata')
        for pin,bits in a['connections'].items():
            if bits==b['connections'][pin]:continue
            if pin not in ('I0','I1','I2','I3') or len(bits)!=1 or len(b['connections'][pin])!=1:raise ValueError('Invalid wide leaf external input')
            source=old_drivers.get(bits[0]);clone=new_drivers.get(b['connections'][pin][0])
            if not exact_clone(source,clone):raise ValueError('Wide leaf source is not an exact ordinary LUT clone')
            changed.append(dict(leaf=n,pin=pin,source=source,clone=clone))
        audit['modules']['top']['cells'][n]=copy.deepcopy(a)
    # Dedicated mux data links remain fixed. Its external selector is an
    # ordinary routed input and may receive the same proved exact clone.
    for n,a in old.items():
        if not a['type'].startswith('MUX2_LUT') or new.get(n)==a:continue
        b=new.get(n)
        if n in bad or b is None or layout.get('mux_clusters',{}).get(n)!=base_layout.get('mux_clusters',{}).get(n):raise ValueError('Moved/protected mux clone substitution')
        if {k:v for k,v in a.items() if k!='connections'}!={k:v for k,v in b.items() if k!='connections'} or set(a['connections'])!=set(b['connections']):raise ValueError('Changed mux primitive')
        if any(a['connections'][p]!=b['connections'][p] for p in a['connections'] if p!='S0') or len(a['connections']['S0'])!=1 or len(b['connections']['S0'])!=1:raise ValueError('Changed dedicated mux data/output link')
        source=old_drivers.get(a['connections']['S0'][0]);clone=new_drivers.get(b['connections']['S0'][0])
        if not exact_clone(source,clone):raise ValueError('Mux selector source is not an exact ordinary LUT clone')
        changed.append(dict(mux=n,pin='S0',source=source,clone=clone));audit['modules']['top']['cells'][n]=copy.deepcopy(a)
    return audit,changed


class SharedDagFeedback(FixedBoundaryCarryFeedback):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        from ownpr_mux_macros import tree
        self.private_leaves=set()
        for macro in self.layout.get('wide_macros',[]):self.private_leaves.update(tree(self.reference,macro['root'],macro['root_slot'])['leaves'])

    def admit(self,design,layout,static):
        audit,substitutions=normalize_clone_inputs(self.reference,design,self.layout,layout,self.private_leaves)
        result=super().admit(audit,layout,static);self.last_macro_input_substitutions=substitutions
        return result
