"""Explicit wide-root FF data relationships at otherwise isolated wide sites."""
from functools import lru_cache
from ownpr_device import ROOT
from ownpr_wide_ff_configuration import groups,audit_capture,ROOT_SLOT


@lru_cache(maxsize=2)
def calibration(dynamic_ce=False):
    return audit_capture(ROOT/('results/native_wide_ff_dynamic_ce_001' if dynamic_ce else 'results/native_wide_ff_encoding_003'))


def pairs(design,placed,device):
    marked=[v for v in placed['clusters'].values() if v.get('wide_ff_root')]
    if not marked:return []
    cells=design['modules']['top']['cells'];macros={m['root']:m for m in placed.get('wide_macros',[])}
    constants={'0':'zero','1':'one'};result=[];seen_ff=set();seen_sites=set()
    for cell in cells.values():
        if cell['type'] in ('GND','VCC'):
            for bs in cell['connections'].values():
                for bit in bs:constants[bit]='one' if cell['type']=='VCC' else 'zero'
    from ownpr_mux_macros import tree
    for row in marked:
        root=row['wide_ff_root'];ff=row['ff'];r,c,s=row['row'],row['column'],row['slot']
        if root not in macros or not ff or ff not in cells:raise ValueError('Missing wide FF logical association')
        m=macros[root];spec=tree(design,root,m['root_slot']);cell=cells[ff]
        if (r,c,s)!=(m['row'],m['column'],m['root_slot']) or spec['leaves'].get(row['lut'])!=s:
            raise ValueError('Wide FF is not at its root-matched leaf slot')
        if (r,c) in seen_sites or ff in seen_ff:raise ValueError('Duplicate wide FF/control group')
        seen_sites.add((r,c));seen_ff.add(ff)
        if cell['type']!='DFFRE' or cell['connections']['D']!=cells[root]['connections']['O'] or int(cell.get('parameters',{}).get('INIT','0'),2)!=0:
            raise ValueError('Uncalibrated wide FF type, data source or initialization')
        profile=dict(cell_type=cell['type'],ce=constants.get(cell['connections']['CE'][0],'signal'),control=constants.get(cell['connections']['RESET'][0],'signal'),clock=constants.get(cell['connections']['CLK'][0],'signal'))
        evidence=calibration(profile['ce']=='signal')
        typ=device.grid[r][c]
        if not any((q['tile_type'],q['level'],q['slot'],q['ff_profile'])==(typ,spec['level'],s,profile) for q in evidence['records']):
            raise ValueError('Wide FF profile/site lacks combined calibration')
        occupants=[v for v in placed['clusters'].values() if (v['row'],v['column'])==(r,c)]
        extras={v['lut']:v for v in occupants if v['lut'] not in spec['leaves']}
        if not set(spec['leaves'])<={v['lut'] for v in occupants} or any(v['ff'] and v is not row for v in occupants):
            raise ValueError('Uncalibrated extra FF at wide FF site')
        sharing=None
        if extras:
            from ownpr_wide_ff_lut_sharing import check as mixed_check
            sharing=mixed_check(design,placed,device,m,spec,extras)
        if any((v['row'],v['column'])==(r,c) for v in placed.get('arithmetic_clusters',{}).values()):raise ValueError('Arithmetic overlaps wide FF site')
        if any(v['ff']==ff and v is not row for v in placed['clusters'].values()):raise ValueError('FF has multiple physical bindings')
        attributes=groups(spec['level'],s,profile)
        for extra in extras.values():attributes[extra['slot']//2][0]=9
        result.append(dict(root=root,ff=ff,leaf=row['lut'],row=r,column=c,slot=s,level=spec['level'],groups=attributes,profile=profile,
            pip=[r,c,'OF'+str(s),'DI'+str(s)],calibration_sha256=evidence['capture_sha256'],ordinary_luts=sorted(extras),sharing_calibration_sha256=sharing))
    return result


def configure_graph(graph,design,placed):
    rows=pairs(design,placed,graph.device)
    if rows:graph.select_terminal_pips([r['pip'] for r in rows])
    return rows
