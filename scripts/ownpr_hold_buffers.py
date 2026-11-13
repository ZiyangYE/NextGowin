"""CEC-proved, fixed-clock hold repair by buffering one reported input branch.

Registers, external pins and cycles are unchanged. The buffers use ordinary
LUTs in free calibrated sites. Complete rerouting and STA are mandatory.
"""
from ownpr_image_storage import read_text as read_artifact_text
import argparse,copy,json,time
from pathlib import Path
from ownpr_route_eco import violations,hold_first_wires
from ownpr_device import ROOT,Device
from ownpr_image_storage import sha
from ownpr_ff_configuration import compile_initial
from ownpr_mux_macros import validate
from native_boundary_cec import prove
from workspace_budget import reserve


def receiver_site(cells,placed,name,pin):
    cell=cells[name]
    if cell['port_directions'].get(pin)!='input':raise ValueError('Invalid buffer receiver input')
    if name in placed['clusters'] and cell['type'] in ('LUT1','LUT2','LUT3','LUT4'):return placed['clusters'][name]
    if name in placed.get('mux_clusters',{}) and cell['type'] in ('MUX2_LUT5','MUX2_LUT6','MUX2_LUT7') and pin=='S0':return placed['mux_clusters'][name]
    if cell['type'] in ('DFFRE','DFFSE') and pin in ('CE','RESET','SET'):
        matches=[r for r in placed['clusters'].values() if r.get('ff')==name]
        if len(matches)==1:return matches[0]
    raise ValueError('Invalid buffer receiver: only ordinary LUT inputs or wide MUX selectors are supported')


def input_branches(text,design,placed,names,limit,allow_internal=False):
    """Deduplicate actual first data edges, never infer a branch from a clock pin."""
    if not 1<=limit<=256:raise ValueError('Branch limit must be 1..256')
    cells=design['modules']['top']['cells'];reverse={v:k for k,v in names.items()}
    traces=hold_first_wires(text);targets=[];seen=set();skipped=[]
    for slack,launch,capture in sorted(violations(text,'Hold')):
        labels=traces.get((launch,capture),[])
        if len(labels)!=1:raise ValueError('Ambiguous or missing hold path branch')
        alias,pin=labels[0].split('/');receiver=reverse[alias];source=reverse[launch.split('/')[0]]
        source_type=cells[source]['type'];output='O' if source_type=='IBUF' else 'Q'
        allowed=source_type=='IBUF' or (allow_internal and source_type in ('DFFRE','DFFSE'))
        if not allowed or cells[receiver]['connections'].get(pin)!=cells[source]['connections'].get(output):
            skipped.append(dict(launch=launch,capture=capture,reason='not_direct_IBUF_branch'));continue
        try:receiver_site(cells,placed,receiver,pin)
        except ValueError:
            skipped.append(dict(launch=launch,capture=capture,reason='not_supported_LUT_or_MUX_selector_receiver'));continue
        key=(receiver,pin)
        if key in seen:continue
        seen.add(key);targets.append(dict(receiver=receiver,pin=pin,source=source,hold_slack_ns=slack))
    if not targets:raise ValueError('No supported reported input hold branch')
    return targets[:limit],dict(supported_branches=len(targets),skipped=skipped)


def insert_buffers(old,placed,device,cal,targets,count):
    if not 1<=count<=8:raise ValueError('Hold buffer count must be 1..8')
    if not targets:raise ValueError('No hold targets')
    new=copy.deepcopy(old);updated=copy.deepcopy(placed);module=new['modules']['top'];changed=module['cells'];cells=old['modules']['top']['cells']
    occupied={(r['row'],r['column'],r['slot']) for r in placed['clusters'].values()}
    excluded={(r['row'],r['column']) for kind in ('arithmetic_clusters','mux_clusters') for r in placed.get(kind,{}).values()}
    for r in placed.get('hard_clusters',{}).values():excluded.update((r['row'],r['column']+i) for i in range(3))
    ground=next(c['connections']['G'][0] for c in cells.values() if c['type']=='GND')
    bits=[b for c in cells.values() for bs in c['connections'].values() for b in bs]
    bits.extend(b for v in module.get('netnames',{}).values() for b in v['bits']);bits.extend(b for v in module['ports'].values() for b in v['bits'])
    nextbit=max(b for b in bits if isinstance(b,int))+1;added=[];seen=set()
    for target in targets:
        receiver,pin=target['receiver'],target['pin'];key=(receiver,pin)
        if key in seen:raise ValueError('Duplicate buffer branch')
        seen.add(key)
        loc=receiver_site(cells,placed,receiver,pin);sites=[]
        for r in range(max(0,loc['row']-3),min(device.rows,loc['row']+4)):
            for c in range(max(0,loc['column']-3),min(device.cols,loc['column']+4)):
                if (r,c) in excluded or device.grid[r][c] not in cal['tile_types']:continue
                sites.extend((abs(r-loc['row'])+abs(c-loc['column']),r,c,s) for s in range(8) if (r,c,s) not in occupied)
        if len(sites)<count:raise ValueError('Insufficient nearby calibrated free LUT slots')
        bit=changed[receiver]['connections'][pin][0];chain=[]
        for _,r,c,s in sorted(sites)[:count]:
            number=len(added);name='ng_hold_buffer_'+str(number)
            while name in changed or name+'_out' in module.get('netnames',{}):number+=1;name='ng_hold_buffer_'+str(number)
            changed[name]=dict(type='LUT4',parameters={'INIT':'1010101010101010'},attributes={},port_directions={**{f'I{i}':'input' for i in range(4)},'F':'output'},
                connections={'I0':[bit],'I1':[ground],'I2':[ground],'I3':[ground],'F':[nextbit]})
            module.setdefault('netnames',{})[name+'_out']=dict(hide_name=1,bits=[nextbit],attributes={})
            updated['clusters'][name]=dict(row=r,column=c,slot=s,lut=name,ff=None);occupied.add((r,c,s))
            added.append(dict(instance=name,site=[r,c,s],receiver=receiver,pin=pin));chain.append(name);bit=nextbit;nextbit+=1
        changed[receiver]['connections'][pin]=[bit]
    updated.update(physical_legality_complete=False,timing_signoff=False);updated.pop('weighted_internal_manhattan',None)
    return new,updated,added


def prepare(base,sta,count,out,branch_limit=1,allow_internal=False):
    start=time.monotonic();base,sta,out=map(Path,(base,sta,out));reserve(ROOT,100_000_000)
    old=json.loads((base/'packed.json').read_text());placed=json.loads((base/'placement.json').read_text());meta=json.loads((sta/'manifest.json').read_text())
    audit=json.loads((sta/'handoff_audit.json').read_text())
    if meta['status']!='imported' or audit['status']!='initial_handoff_checks_passed' or sha(sta/'handoff_audit.json')!=meta['handoff_audit_sha256'] or sha(sta/'probe.tr')!=audit['source_sha256']['probe.tr']:
        raise ValueError('Hold repair requires an unchanged audited native report')
    if meta['packed_sha256']!=sha(base/'packed.json') or meta['placement_sha256']!=sha(base/'placement.json'):raise ValueError('Hold report belongs to another implementation')
    targets,inventory=input_branches(read_artifact_text(sta/'probe.tr'),old,placed,json.loads((sta/'name_map.json').read_text())['instances'],branch_limit,allow_internal)
    device=Device();cal=json.loads((ROOT/'results/native_device_lut_004/lut_encoding_audit.json').read_text())
    if cal['source_fse_sha256']!=device.fse.sha256 or not all(r['match'] for r in cal['checks']):raise ValueError('LUT calibration mismatch')
    new,updated,added=insert_buffers(old,placed,device,cal,targets,count)
    validate(new,updated,device);compile_initial(new,updated)
    from ownpr_carry_macros import validate as carry_validate
    from ownpr_hard_architecture import HardArchitecture
    carry_validate(new,updated,device)
    hard=HardArchitecture().validate(new,updated,device) if updated.get('hard_clusters') else None
    out.mkdir(exist_ok=False);proof=prove(old,new,out/'cec',120)
    for n,d in (('packed.json',new),('placement.json',updated)):(out/n).write_text(json.dumps(d,indent=2))
    result=dict(status='placed',netlist_sha256=sha(out/'packed.json'),placement_sha256=sha(out/'placement.json'),source_sta_sha256=sha(sta/'probe.tr'),
        source_netlist_sha256=sha(base/'packed.json'),source_placement_sha256=sha(base/'placement.json'),
        target=targets[0],targets=targets,inventory=inventory,added=added,proof=proof,hard_resources=hard,internal_branches_enabled=allow_internal,
        cells=len(new['modules']['top']['cells']),ordinary_clusters=len(updated['clusters']),
        clock_endpoints_unchanged=True,fixed_io_unchanged=True,pipeline_stages_added=0,timing_signoff=False,wall_seconds=time.monotonic()-start)
    (out/'manifest.json').write_text(json.dumps(result,indent=2));return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('base','sta','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--count',type=int,default=3);p.add_argument('--branch-limit',type=int,default=1)
    p.add_argument('--internal',action='store_true',help='Also repair direct DFFRE/DFFSE Q data branches; clock pins and direct paired D links are excluded')
    a=p.parse_args();r=prepare(a.base,a.sta,a.count,a.out,a.branch_limit,a.internal);print(json.dumps({k:v for k,v in r.items() if k not in ('proof','hard_resources','added')},indent=2))
