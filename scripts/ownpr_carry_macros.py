"""Own carry macro contracts and capacity checks for the initial mode-2 slice.

Six slots and eastward progression follow retained native evidence. These
checks do not promote untested device-boundary or timing rules to signoff.
"""
from collections import defaultdict
from ownpr_carry_chains import extract
from ownpr_alu_configuration import Mode2Configuration


def contract(design,device,top='top'):
    m=design['modules'][top];cells=m['cells'];data=extract(m)
    if data['blockers']:raise ValueError('Unsupported carry forks')
    constants={'0':0,'1':1};loads=defaultdict(list)
    for name,c in cells.items():
        if c['type'] in ('VCC','GND'):constants[next(iter(c['connections'].values()))[0]]=int(c['type']=='VCC')
        for pin,bits in c['connections'].items():
            if c['port_directions'][pin]=='input':
                for bit in bits:loads[bit].append((name,pin))
    model=Mode2Configuration(device);profiles={}
    for chain in data['chains']:
        if chain['head_constant'] not in ('0','1'):raise ValueError('Uncalibrated dynamic carry-chain head')
        if chain['tail_loads']:raise ValueError('Carry output materialization is not implemented')
        for i,name in enumerate(chain['cells']):
            c=cells[name];mode=int(c['parameters']['ALU_MODE'],2)
            profile=[constants.get(c['connections'][p][0],'signal') for p in ('I0','I1','I3','CIN')]
            model.word(mode,profile);profiles[name]=dict(mode=mode,input_profile=profile)
            expected=[] if i+1==chain['length'] else [(chain['cells'][i+1],'CIN')]
            if loads[c['connections']['COUT'][0]]!=expected:raise ValueError('Uncalibrated carry side fanout')
    return dict(chains=data['chains'],profiles=profiles,calibrated_tile_types=[17],physical_signoff=False)


def validate(design,placement,device,top='top'):
    expected=contract(design,device,top);actual=placement.get('arithmetic_clusters',{})
    if set(actual)!=set(expected['profiles']):raise ValueError('Missing or extra arithmetic placement')
    occupied=set();tiles=set();links=[]
    for chain in expected['chains']:
        head=actual[chain['cells'][0]];r,c=head['row'],head['column']
        if head['slot']!=0:raise ValueError('Initial carry macro must begin at slot zero')
        for index,name in enumerate(chain['cells']):
            row=actual[name];site=(row['row'],row['column'],row['slot'])
            if site!=(r,c+index//6,index%6):raise ValueError('Broken carry macro progression')
            if not 0<=site[0]<device.rows or not 0<=site[1]<device.cols or device.grid[site[0]][site[1]]!=17:raise ValueError('Uncalibrated carry macro tile')
            if site in occupied:raise ValueError('Arithmetic resource collision')
            occupied.add(site);tiles.add(site[:2])
            if row['mode']!=expected['profiles'][name]['mode'] or row['input_profile']!=expected['profiles'][name]['input_profile']:raise ValueError('Arithmetic function/profile changed')
            if index:
                source=chain['cells'][index-1];a=actual[source]
                links.append(dict(source=source,destination=name,source_site=[a['row'],a['column'],a['slot']],destination_site=list(site),
                    kind='within_cfu' if index%6 else 'east_cfu_bridge',physical_signoff=False))
    shared=[];shared_sites=set()
    for cluster in placement['clusters'].values():
        if (cluster['row'],cluster['column']) in tiles:
            if not placement.get('mixed_carry_lut_calibration'):raise ValueError('Initial isolated carry CFU is shared with ordinary logic')
            from ownpr_carry_lut_sharing import check
            digest=check(design,placement,device,cluster);site=(cluster['row'],cluster['column'],cluster['slot'])
            if site in shared_sites:raise ValueError('Mixed carry/LUT site collision')
            shared_sites.add(site);shared.append(dict(lut=cluster['lut'],site=list(site),capture_sha256=digest))
    result=dict(capacity_verified=True,observed_pattern_conformant=True,alu_cells=len(actual),chains=len(expected['chains']),reserved_cfus=len(tiles),
        links=links,arbitrary_boundary_legality_verified=False,timing_signoff=False)
    if shared:result['shared_ordinary_luts']=shared
    return result
