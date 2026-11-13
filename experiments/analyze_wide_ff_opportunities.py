"""Rank current audited FF buffer/wide-root patterns without changing a design."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import load_json,sha
from ownpr_placement_feedback import load
from ownpr_device import Device
from ownpr_wide_ff_configuration import groups
from workspace_budget import reserve


def analyze(placement,sta):
    design=load_json(placement/'packed.json');placed=load_json(placement/'placement.json');meta=load_json(sta/'manifest.json')
    if meta['packed_sha256']!=sha(placement/'packed.json') or meta['placement_sha256']!=sha(placement/'placement.json'):
        raise ValueError('Wide FF opportunity feedback mismatch')
    weights,feedback=load(sta,design,relative_window_ns=1.)
    cells=design['modules']['top']['cells'];device=Device()
    drivers={b:(n,p) for n,c in cells.items() for p,bs in c['connections'].items() if c['port_directions'][p]=='output' for b in bs}
    constants={'0':'zero','1':'one'}
    for c in cells.values():
        if c['type'] in ('GND','VCC'):
            for bs in c['connections'].values():
                for b in bs:constants[b]='one' if c['type']=='VCC' else 'zero'
    macros={m['root']:m for m in placed.get('wide_macros',[])};rows=[]
    for name,site in placed['clusters'].items():
        if not site.get('ff'):continue
        lut=cells[site['lut']];ff=cells[site['ff']]
        if lut['type'] not in ('LUT1','LUT2','LUT3','LUT4') or ff['connections']['D']!=lut['connections']['F']:continue
        width=int(lut['type'][-1]);truth=int(lut['parameters']['INIT'],2)
        pins=[i for i in range(width) if truth==sum(((a>>i)&1)<<a for a in range(1<<width))]
        if len(pins)!=1:continue
        pin='I'+str(pins[0]);source=drivers.get(lut['connections'][pin][0])
        if not source or source[0] not in macros or source[1]!='O':continue
        root=source[0];macro=macros[root];level=int(cells[root]['type'][-1]);r,c,slot=macro['row'],macro['column'],macro['root_slot']
        profile=dict(cell_type=ff['type'],ce=constants.get(ff['connections']['CE'][0],'signal'),control=constants.get(ff['connections'].get('RESET',[None])[0],'signal'),clock=constants.get(ff['connections']['CLK'][0],'signal'))
        rejected=[]
        try:groups(level,slot,profile)
        except ValueError as e:rejected.append(str(e))
        if device.grid[r][c] not in (17,228):rejected.append('Uncalibrated tile type')
        if int(ff.get('parameters',{}).get('INIT','0'),2)!=0:rejected.append('Nondefault FF initialization')
        rows.append(dict(buffer=site['lut'],ff=site['ff'],root=root,level=level,priority=weights.get((name,pin),0.),ff_profile=profile,
            existing_ff_site=[site['row'],site['column'],site['slot']],proposed_root_ff_site=[r,c,slot],tile_type=device.grid[r][c],
            constant_reset_encoding_requires_additional_calibration=profile['control']=='zero',
            clock_group_moves=[site['row'],site['column'],site['slot']//2]!=[r,c,slot//2],rejected=rejected,
            placement_legalized=False,cec_proven=False,full_sta_verified=False))
    rows.sort(key=lambda r:(bool(r['rejected']),-r['priority'],r['buffer']))
    return dict(status='opportunities_only',records=rows,near_critical_candidates=sum(r['priority']>0 and not r['rejected'] for r in rows),
        feedback=feedback,source_sha256={str(p):sha(p) for p in (placement/'packed.json',placement/'placement.json',sta/'probe.tr')},
        scope='Pattern/criticality analysis only. Removing a buffer requires CEC, legal wide/FF packing, clocks and complete routing/STA. Segment delay is not a promised Fmax gain.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--placement',type=Path,required=True);p.add_argument('--sta',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    reserve(ROOT,2_000_000)
    if a.out.exists():raise FileExistsError(a.out)
    result=analyze(a.placement,a.sta);a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(result,indent=2))
    print(json.dumps(dict(status=result['status'],patterns=len(result['records']),near_critical_candidates=result['near_critical_candidates']),indent=2))
