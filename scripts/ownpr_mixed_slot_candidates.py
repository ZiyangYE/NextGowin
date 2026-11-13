"""Design-independent discovery of critical LUTs near spare wide/FF slots.

All incident edges contribute to ranking. This is geometric candidate
generation only; transform validation, loaded routing and full STA follow.
"""
from ownpr_mux_macros import tree
from ownpr_critical_chain_move import locations
from ownpr_timing_pins import pins
from ownpr_wide_ff_lut_sharing import calibration,check,ADMISSION_SHA256


def critical_scores(ranking):
    """Use the actual branch witness, not the worst sibling on its net."""
    nets={r['net']:r for r in ranking['ranked_nets']};scores={};seen=set()
    for path in ranking['traces']:
        for edge in path['ordinary_wires']:
            if edge['net'] not in nets:raise ValueError('Unranked timing witness')
            row=nets[edge['net']];seen.add((edge['net'],edge['endpoint']))
            score=(path['slack_ns'],-edge['wire_delay_ns'])
            for name in (row['driver']['instance'],edge['endpoint'].rsplit('/',1)[0]):
                if name not in scores or score<scores[name]:scores[name]=score
    required={(r['net'],e) for r in ranking['ranked_nets'] for e in r['endpoints']}
    if seen!=required:raise ValueError('Timing branch witness coverage differs')
    return scores


def discover(design,layout,ranking,priorities=None,radius=12,limit=16):
    if not 1<=radius<=32 or not 1<=limit<=64:raise ValueError('Unbounded mixed-slot search')
    from ownpr_device import Device
    device=Device();calibration();priorities=priorities or {};cells=design['modules']['top']['cells'];points=locations(layout)
    macros=[];members=set();blocked_tiles={(v['row'],v['column']) for v in layout.get('arithmetic_clusters',{}).values()}
    for v in layout.get('hard_clusters',{}).values():blocked_tiles.update((v['row'],v['column']+i) for i in range(3))
    view={**layout,'mixed_wide_ff_lut_calibration':ADMISSION_SHA256}
    for m in layout.get('wide_macros',[]):
        spec=tree(design,m['root'],m['root_slot']);members.update(spec['leaves']);members.update(spec['muxes'])
        if spec['level'] not in (5,6):continue
        extras={n:v for n,v in layout['clusters'].items() if (v['row'],v['column'])==(m['row'],m['column']) and n not in spec['leaves']}
        try:check(design,view,device,{**m,'wide_ff_ordinary_sharing':True},spec,extras)
        except ValueError:continue
        macros.append((m,spec))
    drivers={bit:n for n,c in cells.items() for _,bit in pins(c,'output')};edges=[]
    for n,c in cells.items():
        for pin,bit in pins(c,'input'):
            if pin.split('[')[0] in ('CLK','CLKA','CLKB','CIN'):continue
            parent=drivers.get(bit)
            if parent in points and n in points and parent!=n:edges.append((parent,n,1+19*priorities.get((n,pin),0.)**2))
    critical={}
    for n,score in critical_scores(ranking).items():
        loc=layout['clusters'].get(n)
        if loc is None or n in members or loc.get('ff') or cells[n]['type'] not in ('LUT1','LUT2','LUT3','LUT4') or (loc['row'],loc['column']) in blocked_tiles:continue
        critical[n]=score
    occupied={(v['row'],v['column'],v['slot']) for v in layout['clusters'].values()};options=[]
    def distance(a,b):return abs(a[0]-b[0])+abs(a[1]-b[1])
    for n,(slack,negative_wire) in critical.items():
        incident=[e for e in edges if n in e[:2]]
        def cost(point):return sum(w*distance(point if a==n else points[a],point if b==n else points[b]) for a,b,w in incident)
        before=cost(points[n])
        for macro,spec in macros:
            target=(macro['row'],macro['column'])
            if distance(points[n],target)>radius:continue
            after=cost(target)
            if after>=before:continue
            for slot in range(1<<(spec['level']-4),8):
                if (*target,slot) in occupied:continue
                options.append(dict(lut=n,root=macro['root'],slot=slot,old_site=[*points[n],layout['clusters'][n]['slot']],new_site=[*target,slot],worst_reported_slack_ns=slack,reported_wire_ns=-negative_wire,weighted_span_before=before,weighted_span_after=after,span_gain=before-after,incident_edges=len(incident)))
    options.sort(key=lambda r:(r['worst_reported_slack_ns'],-r['reported_wire_ns'],-r['span_gain'],r['lut'],r['root'],r['slot']))
    return dict(eligible_macros=len(macros),critical_ordinary_luts=len(critical),candidate_count=len(options),candidates=options[:limit],timing_gain_predicted=False,design_name_special_cases=False,scope='Slot eligibility plus incident weighted distance; final placement validation and full routed STA mandatory')
