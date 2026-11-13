"""Cross-check native carry placement against untouched engineering netlists.

This is calibration evidence, not a claim of own placement/routing success or
proof that arbitrary device rows admit the observed bridge pattern.
"""
import argparse,hashlib,json,re
from collections import Counter
from pathlib import Path
from ownpr_device import Device,DLL_SHA,ROOT
from ownpr_carry_chains import extract


def audit(folder,netlist,top):
    folder=Path(folder);netlist=Path(netlist);meta=json.loads((folder/'manifest.json').read_text());path=folder/'primitive_bindings.json'
    if (meta.get('status')!='captured' or meta.get('dll_sha256')!=DLL_SHA or meta.get('own_pnr_result') is not False
            or hashlib.sha256(path.read_bytes()).hexdigest()!=meta['bindings_sha256']):raise ValueError('Unverified native carry placement capture')
    if netlist.suffix=='.json':m=json.loads(netlist.read_text())['modules'][top]
    else:
        if hashlib.sha256(netlist.read_bytes()).hexdigest()!=meta['source_sha256']:raise ValueError('Native input netlist changed')
        text=netlist.read_text();cells={}
        for name,body in re.findall(r'\bALU\s+(\S+)\s*\((.*?)\);',text,re.S):
            pins={p:[signal.strip().lstrip('\\')] for p,signal in re.findall(r'\.(\w+)\(([^()]*)\)',body)}
            mode=re.search(r'defparam\s+'+re.escape(name)+r'\.ALU_MODE\s*=\s*(\d+)\s*;',text)
            if not mode:raise ValueError('Missing native serialized ALU mode')
            cells[name]=dict(type='ALU',parameters={'ALU_MODE':format(int(mode[1]),'b')},connections=pins,
                port_directions={p:'output' if p in ('SUM','COUT') else 'input' for p in pins})
        m={'cells':cells}
    chains=extract(m)
    native=json.loads(path.read_text());alus={r['name']:r for r in native if r['logical_class']=='.?AVAlu@NETLIST@@'}
    expected={n for n,c in m['cells'].items() if c['type']=='ALU'}
    if set(alus)!=expected:raise ValueError('Native calibration changed ALU instance set')
    device=Device();sites=set();tile_types=Counter();connections=[]
    lut_sites={tuple(r['physical_header'][1:]) for r in native if r['logical_class']=='.?AVLut4@NETLIST@@' and r.get('physical_header')}
    for name,row in alus.items():
        typ,r,c,s=row['physical_header'];key=(r,c,s)
        if typ!=106 or s not in range(6) or key in sites or key in lut_sites:raise ValueError('ALU/LUT collision or invalid slot')
        if row['alu_init']!=str(int(m['cells'][name]['parameters']['ALU_MODE'],2)):raise ValueError('ALU mode changed')
        sites.add(key);tile_types[device.grid[r-1][c-1]]+=1
    for chain in chains['chains']:
        for source,dest in zip(chain['cells'],chain['cells'][1:]):
            a=alus[source];b=alus[dest];_,r,c,s=a['physical_header'];actual=tuple(b['physical_header'][1:]);predicted=(r,c,s+1) if s<5 else (r,c+1,0)
            if actual!=predicted:raise ValueError('Native carry chain deviates from six-slot/eastward pattern')
            cout=next(p for p in a['pins'] if p['index']==4);cin=next(p for p in b['pins'] if p['index']==3)
            if cout['net']!=cin['net']:raise ValueError('Native carry endpoint net mismatch')
            connections.append(dict(source=source,destination=dest,source_site=[r,c,s],destination_site=list(actual),
                kind='within_cfu' if s<5 else 'east_cfu_bridge',native_direct_query=cin['direct']))
    return dict(status='native_carry_pattern_observed',cells=len(alus),chains=chains['chain_count'],connections=connections,
        within_cfu=sum(r['kind']=='within_cfu' for r in connections),east_bridges=sum(r['kind']=='east_cfu_bridge' for r in connections),
        tile_types=dict(tile_types),source_sha256=dict(bindings=meta['bindings_sha256'],netlist=hashlib.sha256(netlist.read_bytes()).hexdigest()),
        own_pnr_result=False,arbitrary_location_legality_verified=False,configuration_verified=False,
        scope='Observed logical adjacency, physical capacity and six-slot/eastward progression of the fixed-pin native engineering calibration.')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);ap.add_argument('--netlist',type=Path,required=True);ap.add_argument('--top',default='top');a=ap.parse_args();r=audit(a.folder,a.netlist,a.top)
    (a.folder/'carry_placement_audit.json').write_text(json.dumps(r,indent=2));print(json.dumps({k:v for k,v in r.items() if k!='connections'},indent=2))
