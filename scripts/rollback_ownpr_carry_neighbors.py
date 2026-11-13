"""Revert only moved carry-neighbor LUTs appearing on violated STA paths."""
import argparse,copy,json,re,shutil,time
from pathlib import Path
from ownpr_device import ROOT,Device
from ownpr_image_storage import load_json,read_text,sha
from ownpr_timing_numbers import negative_slack

def implicated(text,mapping,moves):
    reverse={v:k for k,v in mapping['instances'].items()};result=set()
    for kind,index in (('Setup',1),('Hold',2)):
        section=re.search(r'^3\.3\.'+str(index)+' '+kind+r' Analysis Report\n(.*?)(?=^3\.|\Z)',text,re.M|re.S)
        if not section:raise ValueError('Missing '+kind+' details')
        for block in re.split(r'^\s*Path\d+\s*$',section[1],flags=re.M):
            slack=re.search(r'^Slack\s*:\s*([-\d.]+)',block,re.M)
            if not slack or not negative_slack(slack[1]):continue
            path=block.split('Data Arrival Path:',1)[-1].split('Data Required Path:',1)[0]
            result.update(reverse[n] for n in re.findall(r'own_i_\d+',path) if reverse.get(n) in moves)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('base','candidate','sta','out'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();start=time.monotonic()
    from workspace_budget import reserve
    reserve(ROOT,30000000);sm=load_json(a.sta/'manifest.json');old=load_json(a.base/'placement.json');new=load_json(a.candidate/'placement.json');d=load_json(a.candidate/'packed.json')
    if sha(a.base/'packed.json')!=sha(a.candidate/'packed.json') or sm['packed_sha256']!=sha(a.candidate/'packed.json') or sm['placement_sha256']!=sha(a.candidate/'placement.json') or not sm['sta_completed'] or sm['vendor_routing_executed']:raise ValueError('Rollback feedback contract differs')
    changes=load_json(a.candidate/'changes.json');names={r['lut'] for r in changes};selected=implicated(read_text(a.sta/'probe.tr'),load_json(a.sta/'name_map.json'),names)
    if not selected:raise ValueError('No moved carry neighbor on failing paths')
    result=copy.deepcopy(new)
    for name in selected:result['clusters'][name]=copy.deepcopy(old['clusters'][name])
    sites=[(r['row'],r['column'],r['slot']) for r in result['clusters'].values()]
    if len(sites)!=len(set(sites)):raise ValueError('Rollback destination occupied')
    from ownpr_carry_macros import validate
    from ownpr_mux_macros import validate as mux
    from ownpr_ff_configuration import compile_initial
    dev=Device();audit=validate(d,result,dev);mux(d,result,dev);compile_initial(d,result)
    if result.get('hard_clusters'):
        from ownpr_hard_architecture import HardArchitecture
        HardArchitecture().validate(d,result,dev)
    result.update(timing_signoff=False,physical_legality_complete=False)
    a.out.mkdir(parents=True,exist_ok=False);shutil.copyfile(a.candidate/'packed.json',a.out/'packed.json');(a.out/'placement.json').write_text(json.dumps(result,indent=2))
    retained=[r for r in changes if r['lut'] not in selected];(a.out/'changes.json').write_text(json.dumps(retained,indent=2));shutil.copyfile(__file__,a.out/'producer_source.py.txt')
    m=load_json(a.candidate/'manifest.json');m.update(placement_sha256=sha(a.out/'placement.json'),netlist_sha256=sha(a.out/'packed.json'),rollback_luts=sorted(selected),carry_neighbor_moves=retained,source_failed_placement_sha256=sha(a.candidate/'placement.json'),source_sta_sha256=sha(a.sta/'probe.tr'),carry_audit=audit,wall_seconds=time.monotonic()-start,timing_signoff=False)
    (a.out/'manifest.json').write_text(json.dumps(m,indent=2));print(json.dumps(dict(rolled_back=sorted(selected),retained=len(retained)),indent=2))

if __name__=='__main__':main()
