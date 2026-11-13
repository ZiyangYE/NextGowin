"""Same-slot mode-2 carry translation under fixed FF/IO/clock boundaries.

The independent native calibration supplies local ALU/bridge timing. Every
export still rechecks actual placed queries and full native setup/hold.
"""
import copy
from ownpr_lut_graph_feedback import FixedBoundaryLutFeedback
from ownpr_image_storage import load_json,sha
from ownpr_carry_chains import extract
from ownpr_carry_macros import validate as validate_carry
from ownpr_carry_lut_sharing import calibration
from ownpr_extended_timing_model import ExtendedTimingModel


def rigid_offsets(chains,before,after):
    if set(before)!=set(after):raise ValueError('Changed arithmetic instance set')
    result=[]
    for chain in chains:
        offsets=set()
        for name in chain['cells']:
            a,b=before[name],after[name]
            if {k:v for k,v in a.items() if k not in ('row','column')}!={k:v for k,v in b.items() if k not in ('row','column')}:raise ValueError('Carry slot/mode/profile changed')
            offsets.add((b['row']-a['row'],b['column']-a['column']))
        if len(offsets)!=1:raise ValueError('Partial or non-rigid carry-chain movement')
        offset=offsets.pop()
        if offset!=(0,0):result.append(dict(head=chain['cells'][0],cells=chain['cells'],offset=list(offset)))
    return result


class FixedBoundaryCarryFeedback(FixedBoundaryLutFeedback):
    def __init__(self,model,graphs,reference,layout,static,library,device):
        super().__init__(model,graphs,reference,layout,static,library,device)
        self.chains=extract(reference['modules']['top'])['chains'];self.alu_library=ExtendedTimingModel()
        self.audit_parent=FixedBoundaryLutFeedback(model,graphs,reference,self.normalize_layout(layout),self.normalize_static(static),library,device)
        for mode,graph in graphs.items():
            for edge in graph['edges']:
                src,pin=edge['source'].rsplit('/',1);dst,dp=edge['destination'].rsplit('/',1)
                if edge['kind']=='cell' and reference['modules']['top']['cells'][src]['type']=='ALU':
                    site=layout['arithmetic_clusters'][src];key=(site['mode'],site['slot'],pin,dp,2 if mode=='hold' else 1);values=self.alu_library.alu.get(key)
                    expected=values[:2] if mode=='hold' and values is not None else values[2:4] if values is not None else None
                    if expected is None or edge['delay']!=expected:raise ValueError('Baseline ALU arc differs from independent native calibration')

    @staticmethod
    def normalize_layout(layout):
        result=copy.deepcopy(layout);result['arithmetic_clusters']={}
        # Actual mixed-mode calibration is checked before entering the parent
        # ordinary-LUT admission; both audit views use the same valid marker.
        result['mixed_carry_lut_calibration']=calibration()['capture_sha256']
        return result

    @staticmethod
    def normalize_static(static):
        return [{k:v for k,v in row.items() if row.get('kind')!='dedicated_carry_link' or k not in ('source_site','destination_site')} for row in static]

    def admit(self,design,layout,static):
        offsets=rigid_offsets(self.chains,self.layout.get('arithmetic_clusters',{}),layout.get('arithmetic_clusters',{}))
        if any(n in self.protected for row in offsets for n in row['cells']):raise ValueError('Moved protected ALU')
        checked=validate_carry(design,layout,self.device)
        expected={(r['source'],r['destination']):(r['source_site'],r['destination_site']) for r in checked['links']}
        observed={}
        for row in static:
            if row.get('kind')!='dedicated_carry_link':continue
            key=row['source'],row['destination']
            if key in observed:raise ValueError('Duplicate static carry link')
            observed[key]=(row['source_site'],row['destination_site'])
        if observed!=expected:raise ValueError('Static carry links do not match actual macro geometry')
        # No state moves or static-link changes are hidden: only fully checked
        # arithmetic geometry is removed from these ordinary-LUT audit views.
        self.audit_parent.admit(design,self.normalize_layout(layout),self.normalize_static(static))
        for graph in self.graphs.values():
            for edge in graph['edges']:
                src,pin=edge['source'].rsplit('/',1);dst,dp=edge['destination'].rsplit('/',1)
                if edge['kind']=='wire' and dp=='CIN' and src in layout.get('arithmetic_clusters',{}):
                    if (src,dst) not in expected or edge['delay']!=[0.,0.]:raise ValueError('Uncalibrated dedicated carry timing')
        return offsets
