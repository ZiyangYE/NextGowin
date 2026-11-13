"""Generate bounded synthesis candidates from audited routed STA evidence."""
from ownpr_image_storage import read_text as read_artifact_text
import argparse,json,re
from pathlib import Path
from extract_timing import ROW
from ownpr_place import sha
from ownpr_placement_feedback import load as checked_feedback
from ownpr_mux_macros import tree
from ownpr_mux_architecture import MuxArchitecture
from ownpr_wide_cone import replace_pair
from reorder_ownpr_wide_inputs import reorder
from ownpr_timing_numbers import negative_slack


def read(p):
    from ownpr_image_storage import load_json
    return load_json(p)

def selector_costs(design,placed,root,architecture):
    """Worst calibrated cell-only path from each selector level to macro output."""
    spec=tree(design,root);locations=placed['mux_clusters']
    parents={r['source']:(r['destination'],r['pin']) for r in spec['links'] if r['source'] in spec['muxes']}
    costs={}
    for name,row in spec['muxes'].items():
        loc=locations[name];value=max(architecture.delay(17,loc['level'],loc['slot'],'S0',1)[2:]);node=name
        while node!=root:
            node,pin=parents[node];loc=locations[node]
            value+=max(architecture.delay(17,loc['level'],loc['slot'],pin,1)[2:])
        index=row['level']-1;costs[index]=max(costs.get(index,0.),value)
    return costs


def plan(placement,routes,sta,max_candidates=12,max_growth=8,include_moves=False,include_prune=False,include_selectors=False):
    placement,routes,sta=map(Path,(placement,routes,sta))
    design=read(placement/'packed.json');placed=read(placement/'placement.json');meta=read(sta/'manifest.json')
    if (meta['packed_sha256']!=sha(placement/'packed.json') or meta['placement_sha256']!=sha(placement/'placement.json')
        or meta['routes_sha256']!=sha(routes/'routes.json') or meta['clock_tree_sha256']!=sha(routes/'clock_tree.json')):
        raise ValueError('Joint feedback inputs differ from audited physical implementation')
    if max_candidates<1 or max_growth<0:raise ValueError('Invalid joint candidate budget')
    _,feedback=checked_feedback(sta,design)
    cells=design['modules']['top']['cells'];mapping=read(sta/'name_map.json');reverse={v:n for n,v in mapping['instances'].items()}
    report=read_artifact_text(sta/'probe.tr');section=re.search(r'^3\.3\.1 Setup Analysis Report\n(.*?)(?=^3\.|\Z)',report,re.M|re.S)
    if not section:raise ValueError('Missing joint feedback setup analysis')
    blocks=re.split(r'^\s*Path(\d+)\s*$',section[1],flags=re.M);paths=[];coverage=read(sta/'handoff_audit.json')['timing']['path_coverage']['setup']['paths']
    if (len(blocks)-1)//2!=coverage:raise ValueError('Incomplete joint feedback setup paths')
    def rows(text):
        result=[]
        for line in text.splitlines():
            m=ROW.match(line)
            if not m:continue
            arrival,delay,kind,transition,fanout,location,endpoint=m.groups();alias,_,pin=endpoint.rpartition('/')
            if alias not in reverse:continue
            result.append(dict(instance=reverse[alias],pin=pin,arrival_ns=float(arrival),delay_ns=float(delay),kind=kind,transition=transition,fanout=int(fanout),location=location))
        return result
    for i in range(1,len(blocks),2):
        body=blocks[i+1];slack=re.search(r'^Slack\s*:\s*([-\d.]+)',body,re.M)
        if not slack:raise ValueError('Uncovered setup slack in joint feedback')
        if not negative_slack(slack[1]):continue
        arrival=re.search(r'Data Arrival Path:\n(.*?)(?=\nData Required Path:)',body,re.S)
        required=re.search(r'Data Required Path:\n(.*?)(?=\nPath Statistics:|\Z)',body,re.S)
        if not arrival or not required:raise ValueError('Incomplete setup trace in joint feedback')
        data=rows(arrival[1]);req=rows(required[1]);paths.append(dict(path=int(blocks[i]),slack_ns=float(slack[1]),arrival=data,
            data_wire_ns=round(sum(r['delay_ns'] for r in data if r['kind']=='tNET' and r['pin']!='CLK'),6),
            launch_clock_wire_ns=round(sum(r['delay_ns'] for r in data if r['kind']=='tNET' and r['pin']=='CLK'),6),
            capture_clock_wire_ns=round(sum(r['delay_ns'] for r in req if r['kind']=='tNET' and r['pin']=='CLK'),6),
            logic_cell_ns=round(sum(r['delay_ns'] for r in data if r['kind']=='tINS' and cells[r['instance']]['type'] not in ('IBUF','OBUF')),6),
            io_cell_ns=round(sum(r['delay_ns'] for r in data if r['kind']=='tINS' and cells[r['instance']]['type'] in ('IBUF','OBUF')),6)))
    macro_owner={};macro_nodes={};macro_costs={};arch=MuxArchitecture()
    for macro in placed.get('wide_macros',[]):
        root=macro['root'];spec=tree(design,root);nodes=set(spec['leaves'])|set(spec['muxes']);macro_nodes[root]=nodes
        macro_costs[root]=selector_costs(design,placed,root,arch)
        for name in nodes:macro_owner[name]=root
    drivers={b[0]:(n,p) for n,c in cells.items() for p,b in c['connections'].items() if c['port_directions'][p]=='output'}
    proposals={};rejected=[]
    def add(key,item,path,gain):
        if key not in proposals:proposals[key]=dict(key=key,**item,worst_slack_ns=path['slack_ns'],estimated_local_gain_ns=round(gain,6),witness_paths=[])
        record=proposals[key];record['worst_slack_ns']=min(record['worst_slack_ns'],path['slack_ns']);record['estimated_local_gain_ns']=max(record['estimated_local_gain_ns'],round(gain,6))
        if path['path'] not in record['witness_paths']:record['witness_paths'].append(path['path'])
    for path in paths:
        for index,row in enumerate(path['arrival']):
            name,pin=row['instance'],row['pin'];cell=cells[name]
            if row['kind']!='tNET' or cell['port_directions'].get(pin)!='input' or pin=='CLK':continue
            if cell['type']=='OBUF' and pin=='I' and name in placed['fixed_io'] and row['delay_ns']>.001:
                driver,driver_pin=drivers.get(cell['connections']['I'][0],(None,None))
                if driver and driver not in placed.get('ioreg_clusters',{}) and driver_pin=='Q':
                    port=placed['fixed_io'][name]['port'];key=f'output_ioreg:{port}'
                    if key not in proposals:
                        from ownpr_ioreg_macros import relocate
                        try:_,moves,_=relocate(design,placed,[port])
                        except ValueError as error:rejected.append(dict(key=key,reason=str(error)))
                        else:add(key,dict(kind='output_ioreg',port=port,ff=driver,lut_growth=0,
                            old_site=moves[0]['old_core_site'],new_site=moves[0]['new_iob_site'],
                            clock_and_input_path_changes_not_estimated=True),path,row['delay_ns'])
            if name in macro_owner and (re.fullmatch('I[0-3]',pin) and cell['type']=='LUT4' or pin=='S0'):
                root=macro_owner[name];bit=cell['connections'][pin][0]
                if name==root and pin=='S0':continue
                finish=next((j for j in range(index+1,len(path['arrival'])) if path['arrival'][j]['instance']==root and path['arrival'][j]['pin']=='O' and path['arrival'][j]['kind']=='tINS'),None)
                if finish is None:continue
                old=sum(r['delay_ns'] for r in path['arrival'][index+1:finish+1] if r['kind']=='tINS')
                final_index=placed['mux_clusters'][root]['level']-1
                for target,new in macro_costs[root].items():
                    gain=old-new
                    if gain<=.001:continue
                    key=f'wide_reorder:{root}:{bit}'+('' if target==final_index else f':select{target+1}')
                    try:_,change=reorder(design,root,bit,target)
                    except ValueError as e:rejected.append(dict(key=key,reason=str(e)));continue
                    add(key,dict(kind='wide_reorder',root=root,late_bit=bit,target_index=target,
                        displaced_bit=change['old_order'][target],predicted_selector_cell_path_ns=new,
                        lut_growth=0,source_instance=name,source_pin=pin),path,gain)
            if (name not in macro_owner and cell['type'] in ('LUT1','LUT2','LUT3','LUT4') and pin.startswith('I')
                    and name in placed['clusters'] and placed['clusters'][name]['ff'] is None):
                if include_selectors:
                    from ownpr_selector_cascade import replace as selector_replace
                    bit=cell['connections'][pin][0];key=f'selector_cascade:{name}:{bit}'
                    try:
                        converted,change=selector_replace(design,name,bit,1);spec=tree(converted,name);root_site=spec['muxes'][name]
                        new=max(arch.delay(17,root_site['level'],root_site['slot'],'S0',1)[2:])
                        old=next((r['delay_ns'] for r in path['arrival'][index+1:] if r['instance']==name and r['pin']=='F' and r['kind']=='tINS'),0.)
                        if change['lut_growth']<=max_growth and old-new>.001:
                            add(key+':stages1',dict(kind='selector_cascade',root=name,late_bit=bit,selector_count=1,lut_growth=change['lut_growth'],
                                predicted_selector_cell_path_ns=new,competing_inputs_and_routing_not_estimated=True),path,old-new)
                    except ValueError:pass
                parent,pout=drivers.get(cell['connections'][pin][0],(None,None))
                if parent is None or parent in macro_owner or cells[parent]['type'] not in ('LUT1','LUT2','LUT3','LUT4'):continue
                key=f'wide_cut:{parent}:{name}'
                if key in proposals:continue
                try:_,change=replace_pair(design,parent,name)
                except ValueError:continue
                growth=(1<<(change['cut_width']-4))-1
                if growth>max_growth:continue
                add(key,dict(kind='wide_cut',parent=parent,root=name,cut_width=change['cut_width'],lut_growth=growth),path,row['delay_ns'])
    if include_prune:
        from prune_ownpr_lut_inputs import prune
        _,changes=prune(design);unused={(r['instance'],r['pin']) for r in changes}
        if changes and paths:
            key='lut_input_prune';witness=[];gain=0.
            for path in paths:
                for i,row in enumerate(path['arrival']):
                    if row['kind']=='tNET' and (row['instance'],row['pin']) in unused:
                        witness.append(path['path']);gain=max(gain,row['delay_ns'])
            proposals[key]=dict(kind=key,key=key,lut_growth=0,pruned_inputs=len(changes),affected_luts=len({r['instance'] for r in changes}),
                worst_slack_ns=min(p['slack_ns'] for p in paths),estimated_local_gain_ns=gain,witness_paths=sorted(set(witness)))
    sources=[placement/'packed.json',placement/'placement.json',routes/'routes.json',routes/'clock_tree.json',sta/'probe.tr',sta/'handoff_audit.json']
    if include_moves:
        from ownpr_wide_macro_move import proposals as macro_moves
        from ownpr_device import Device
        endpoint_file=sta/'sta_endpoint_audit.json'
        if read(sta/'handoff_audit.json')['source_sha256']['sta_endpoint_audit.json']!=sha(endpoint_file):raise ValueError('Macro move endpoint evidence changed')
        sources.append(endpoint_file)
        for item in macro_moves(design,placed,Device(),paths,read(endpoint_file)):proposals[item['key']]=item
    ordered=sorted(proposals.values(),key=lambda r:(r['worst_slack_ns'],-r['estimated_local_gain_ns'],r['lut_growth'],r['key']))
    return dict(status='audited_physical_feedback_candidates',candidates=ordered[:max_candidates],available_candidates=len(ordered),negative_setup_paths=paths,
        rejected=rejected,source_sha256={str(p.resolve()):sha(p) for p in sources},
        feedback=feedback,ranking='worst path slack, local heuristic gain, LUT growth; predicted gain is not full STA',
        vendor_pnr_executed=False,timing_signoff=False,scope='Supported wide rewrites and exclusive fixed-pad output-register packing; no general hard-resource or arbitrary clock-mode search.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--placement',type=Path,required=True);p.add_argument('--routes',type=Path,required=True);p.add_argument('--sta',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--max-candidates',type=int,default=12);a=p.parse_args();r=plan(a.placement,a.routes,a.sta,a.max_candidates)
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(r,indent=2));print(json.dumps(r['candidates'],indent=2))
