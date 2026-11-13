"""Joint identity removal and ordinary-LUT relocation at fixed FF sites.

Produces explicit unrouted candidates only. Callers must attach the calibrated
mode contract and complete routing/export/STA admission; no Fmax is claimed.
"""
import collections,copy

LUTS={'LUT1','LUT2','LUT3','LUT4'}


def candidates(design,placed,device,radius=8):
    cells=design['modules']['top']['cells'];clusters=placed['clusters'];loads=collections.defaultdict(list)
    for name,c in cells.items():
        for pin,bits in c['connections'].items():
            if c['port_directions'][pin]=='input':
                for bit in bits:loads[bit].append((name,pin))
    reserved={(p['row'],p['column']) for key in ('arithmetic_clusters','mux_clusters') for p in placed.get(key,{}).values()}
    reserved.update((p['row'],p['column']+i) for p in placed.get('hard_clusters',{}).values() for i in range(3))
    protected={n for macro in placed.get('wide_macros',[]) for n,p in clusters.items() if (p['row'],p['column'])==(macro['row'],macro['column'])}
    outputs={b for p in design['modules']['top']['ports'].values() for b in p['bits']}
    constants={'0','1'}|{b for c in cells.values() if c['type'] in ('GND','VCC') for bs in c['connections'].values() for b in bs}
    result=[]
    for buffer,site in clusters.items():
        if buffer in protected or not site.get('ff') or site.get('wide_ff_root') or site.get('independent_ff_input') or cells[buffer]['type'] not in LUTS:continue
        r,c,slot=site['row'],site['column'],site['slot'];ff=site['ff'];cell=cells[buffer]
        if (r,c) in reserved or device.grid[r][c] not in (17,228):continue
        width=int(cell['type'][-1]);truth=int(cell['parameters']['INIT'],2)
        inputs=[i for i in range(width) if truth==sum(((a>>i)&1)<<a for a in range(1<<width))]
        if len(inputs)!=1:continue
        source=cell['connections']['I'+str(inputs[0])][0];dest=cell['connections']['F'][0]
        # Native STA omits a direct Q->D self-feedback endpoint after removing
        # its explicit identity LUT. Do not waive endpoint coverage to admit it.
        if source in constants or cells[ff]['connections']['Q']==[source]:continue
        if loads[dest]!=[(ff,'D')] or dest in outputs or cells[ff]['connections']['D']!=[dest]:continue
        for lut,target in clusters.items():
            if lut==buffer or lut in protected or target.get('ff') or target.get('wide_ff_root') or cells[lut]['type'] not in LUTS:continue
            if (target['row'],target['column']) in reserved or cells[lut]['connections']['F']==[source]:continue
            distance=abs(r-target['row'])+abs(c-target['column'])
            if distance>radius:continue
            result.append(dict(buffer=buffer,lut=lut,ff=ff,input_bit=source,removed_bit=dest,ff_site=[r,c,slot],old_lut_site=[target['row'],target['column'],target['slot']],distance=distance))
    return sorted(result,key=lambda x:(x['distance'],x['buffer'],x['lut']))


def transform(design,placed,choice,device):
    # Regenerate the exact eligible choice to reject stale/invalid requests.
    if choice not in candidates(design,placed,device,radius=choice['distance']):raise ValueError('Independent input candidate is no longer eligible')
    new=copy.deepcopy(design);layout=copy.deepcopy(placed);cells=new['modules']['top']['cells'];buffer,lut,ff=choice['buffer'],choice['lut'],choice['ff']
    old_ff_sites={r['ff']:(r['row'],r['column'],r['slot']) for r in placed['clusters'].values() if r.get('ff')}
    cells[ff]['connections']['D']=[choice['input_bit']];del cells[buffer];del layout['clusters'][buffer]
    r,c,s=choice['ff_site'];layout['clusters'][lut].update(row=r,column=c,slot=s,ff=ff,independent_ff_input=True)
    actual={p['ff']:(p['row'],p['column'],p['slot']) for p in layout['clusters'].values() if p.get('ff')}
    if actual!=old_ff_sites:raise ValueError('Independent packing changed FF locations')
    points=[(p['row'],p['column'],p['slot']) for p in layout['clusters'].values()]
    if len(points)!=len(set(points)):raise ValueError('Independent packing overlaps LUT slots')
    return new,layout,dict(**choice,lut_growth=-1,ff_locations_unchanged=True,interface_cycles_changed=False,clock_endpoints_unchanged=True,requires_independent_input_backend=True)
