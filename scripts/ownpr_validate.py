"""Independent capacity/endpoint validation for the initial own P&R slice."""
import json,re
from ownpr_ff_configuration import FF_CONTROLS

def placement(design,placed,native_grid,native_iob,pin_lookup,contract,top='top'):
    cells=design['modules'][top]['cells'];seen_cells=set();sites=set();controls=set()
    for group in placed['clusters'].values():
        r,c,i=group['row'],group['column'],group['slot'];name=group['lut'];ff=group['ff']
        if not (0<=r<len(native_grid)-2 and 0<=c<len(native_grid[0])-2 and 0<=i<8) or native_grid[r+1][c+1]!=5:raise ValueError('Non-CFU LUT site')
        if (r,c,i) in sites:raise ValueError('LUT site collision')
        sites.add((r,c,i))
        if name in seen_cells or name not in cells or cells[name]['type'] not in ('LUT1','LUT2','LUT3','LUT4'):raise ValueError('Invalid/duplicate LUT binding')
        seen_cells.add(name)
        if ff:
            if ff in seen_cells or ff not in cells or cells[ff]['type'] not in FF_CONTROLS:raise ValueError('Invalid/duplicate FF binding')
            if i not in (0,2,4):
                from ownpr_ff_configuration import single_ff_any_slot_calibration
                if placed.get('single_ff_any_slot_calibration')!=single_ff_any_slot_calibration() or sum(bool(p.get('ff')) and (p['row'],p['column'])==(r,c) for p in placed['clusters'].values())!=1:raise ValueError('Uncalibrated extended single-FF control group placement')
            if (r,c,i//2) in controls:raise ValueError('Unsupported/conflicting FF control group')
            controls.add((r,c,i//2));seen_cells.add(ff)
            if cells[ff]['connections']['D']!=cells[name]['connections']['F']:raise ValueError('FF not driven by its paired LUT')
    expected={n for n,c in cells.items() if c['type'].startswith('LUT') or c['type'] in FF_CONTROLS}
    if seen_cells!=expected:raise ValueError('Missing or extra physical logic binding')
    io={(r['native_row'],r['native_column'],r['slot']):r['buffers'] for r in native_iob};used_io=set();observed_ports=set()
    for name,b in placed['fixed_io'].items():
        if name not in cells or cells[name]['type'] not in ('IBUF','OBUF'):raise ValueError('Unknown I/O cell')
        c=cells[name];pad='I' if c['type']=='IBUF' else 'O';port=contract['ports'][c['connections'][pad][0]]
        if b['port']!=port or b['package_pin']!=contract['pins'][port]:raise ValueError('Frozen package pin changed')
        if b['electrical']!=contract['properties'].get(port,''):raise ValueError('Electrical constraint changed')
        site=pin_lookup(b['package_pin'])
        if site!=b['physical_site'] or site in used_io:raise ValueError('I/O site mismatch/collision')
        used_io.add(site);observed_ports.add(port)
        side,index,letter=re.fullmatch(r'IO([TBLR])(\d+)([AB])',site).groups();index=int(index)
        r,col={'T':(0,index),'B':(len(native_grid)-1,index),'L':(index,0),'R':(index,len(native_grid[0])-1)}[side]
        buffers=io.get((r,col,ord(letter)-65))
        if not buffers or len(buffers)!=1 or b['native_ports']!=buffers[0]:raise ValueError('I/O pin-to-wire binding changed')
    if observed_ports!=set(contract['ports'].values()):raise ValueError('External port omitted')
    return dict(capacity_verified=True,paired_ff_data_verified=True,fixed_package_pins_verified=True,
        electrical_constraints_verified=True,native_iob_endpoints_verified=True,logic_instances=len(seen_cells),io_buffers=len(used_io),
        scope='Initial LUT4/DFFRE slice capacity and endpoints only. Does not certify all physical configuration rules or timing.')
