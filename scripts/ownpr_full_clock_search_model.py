"""Complete synchronous setup/hold scoring for the existing clock-tree search."""
from ownpr_clock_data_model import ClockDataModel
from ownpr_bidir_timing import analyze


class FullClockSearchModel:
    def __init__(self,model,design,capture):
        cells=design['modules']['top']['cells']
        if any(c['type'] in ('DFFCE','DFFPE') for c in cells.values()):raise ValueError('Async clocks need explicit recovery/removal model before admission')
        self.full=ClockDataModel(model,design,capture);self.base=self.full.base
        detailed=analyze(model['late'],model['setup_requirements']);self.priority={}
        for ff,node in self.full.clocks.items():
            values=[min(detailed['slack'][ff+'/Q'])]
            values.extend(v for n,v in detailed['endpoints'].items() if n.rsplit('/',1)[0]==ff)
            self.priority[node]=min(self.priority.get(node,float('inf')),min(values))

    def priorities(self):return self.priority

    def evaluate(self,rows):
        result=self.full.measured(rows);s,h=result['setup'],result['hold'];point=min(s['endpoints'],key=s['endpoints'].get)
        return dict(setup_wns_ns=s['wns'],setup_violations=s['violations'],total_negative_setup_slack_ns=s['tns'],
            worst_endpoint=dict(endpoint=point,slack_ns=s['endpoints'][point]),
            conservative_bounds_ns=dict(hold=h['wns'],recovery=None,removal=None),
            conservative_checks_passed=h['wns']>=.001 and not h['violations'],
            complete_hold_wns_ns=h['wns'],complete_hold_violations=h['violations'],
            timing_signoff=False,scope='All synchronous native-model rise/fall endpoints and competing paths; exact fixed-data clock deltas, no async/multi-clock/pulse-width signoff.')
