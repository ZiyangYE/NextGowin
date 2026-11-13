"""Replicate supported original state inputs near newly mapped wide macros.

Uses checked historical input headroom to screen candidates, then geometry to
rank/place them. Neither is a current loaded timing guarantee. The caller must
prove the combined logic/state change and rerun clocks, routing and full STA.
"""
import math
from ownpr_critical_chain_move import locations
from ownpr_mux_macros import tree
from ownpr_regional_replication import replicate


def candidates(before,design,layout,changes,budgets,period_reduction_ns,count=8,min_span=8,min_input_setup=.5):
    if not 1<=count<=8 or not 1<=min_span<=100 or not math.isfinite(period_reduction_ns) or not 0<=min_input_setup<=5:raise ValueError('Invalid frontier limits')
    cells=design['modules']['top']['cells'];original=before['modules']['top']['cells'];points=locations(layout)
    drivers={b:n for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    constants={'0','1'}
    for c in cells.values():
        if c['type'] in ('GND','VCC'):constants.update(b for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs)
    paired={p['ff']:n for n,p in layout['clusters'].items() if p.get('ff') and not p.get('wide_ff_root') and cells[n]['type'] in ('LUT1','LUT2','LUT3','LUT4')}
    options=[];rejected=[]
    for change in changes:
        root=change['logical']['root']
        if cells[root]['type'] not in ('MUX2_LUT5','MUX2_LUT6','MUX2_LUT7'):continue
        spec=tree(design,root);groups={}
        for n in sorted(set(spec['leaves'])|set(spec['muxes'])):
            for pin,bs in cells[n]['connections'].items():
                if cells[n]['port_directions'][pin]!='input' or n in spec['muxes'] and pin!='S0':continue
                parent=drivers.get(bs[0])
                if parent in original and parent in paired and cells[parent]['type'] in ('DFFRE','DFFSE'):
                    groups.setdefault(parent,[]).append((n,pin))
        for parent,sinks in sorted(groups.items()):
            span=sum(abs(a-b) for a,b in zip(points[parent],points[root]))
            if span<min_span:continue
            ff=cells[parent];headroom={};reason=None
            if ff!=original[parent]:reason='Original register contract changed'
            for pin in ('D','CE','RESET' if ff['type']=='DFFRE' else 'SET'):
                if ff['connections'][pin][0] in constants:continue
                slack=budgets.get((parent,pin),{}).get('original_setup_slack_ns')
                if slack is None or not math.isfinite(slack):reason='Missing checked input slack';break
                headroom[pin]=slack-max(0.,period_reduction_ns)
                if headroom[pin]<min_input_setup:reason='Input is already too tight at target period';break
            if reason:
                rejected.append(dict(root=root,parent=parent,span=span,target_input_headroom_ns=headroom,reason=reason));continue
            q=budgets.get((parent,'Q'),{}).get('original_setup_slack_ns')
            options.append(dict(root=root,parent=parent,sinks=sorted(sinks),old_span=span,target_input_headroom_ns=headroom,source_q_slack_ns=q))
    options.sort(key=lambda r:(-r['old_span'],r['source_q_slack_ns'] if r['source_q_slack_ns'] is not None else float('inf'),r['root'],r['parent']))
    return options,rejected


def apply(before,design,layout,changes,budgets,device,period_reduction_ns,count=8,min_span=8,min_input_setup=.5,protected=()):
    options,rejected=candidates(before,design,layout,changes,budgets,period_reduction_ns,count,min_span,min_input_setup)
    after,placed=design,layout;selected=[];aliases={}
    for row in options:
        if len(selected)>=count:break
        try:
            changed,proposal,record=replicate(after,placed,device,row['parent'],row['sinks'],allowed_registers={row['parent']},cluster_region=True,protected_cells=protected)
            points=locations(proposal);clone=next(iter(record['register_replicas']));new_span=max(sum(abs(a-b) for a,b in zip(points[clone],points[n])) for n,p in row['sinks'])
            if new_span>=row['old_span']:raise ValueError('Replica does not reduce source-to-macro span')
        except ValueError as error:
            rejected.append(dict(row,reason=str(error),stage='replica_placement'));continue
        after,placed=changed,proposal;aliases.update(record['register_replicas']);selected.append(dict(row,new_span=new_span,change=record))
    return after,placed,dict(selected=selected,rejected=rejected,register_replicas=aliases,candidate_count=len(options),requested_count=count,
        min_span=min_span,min_input_setup_ns=min_input_setup,period_reduction_ns=period_reduction_ns,
        scope='Historical source-input screening and source-to-macro geometry only; new D/control loading, clock skew, setup/hold require full routed STA.')
