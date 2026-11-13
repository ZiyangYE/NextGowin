"""Validated native ALU pin binding; carry-chain placement remains explicit."""
import hashlib,json
from pathlib import Path
from ownpr_device import ROOT,DLL_SHA


class AluArchitecture:
    def __init__(self,folder=ROOT/'results/native_alu_inventory_003'):
        folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());path=folder/'alu_bindings.json'
        if (meta.get('status')!='captured' or meta.get('dll_sha256')!=DLL_SHA or meta.get('vendor_pnr_executed') is not False
                or meta.get('open_chip_database_used') is not False or hashlib.sha256(path.read_bytes()).hexdigest()!=meta.get('bindings_sha256')):
            raise ValueError('Unverified ALU binding capture')
        self.bindings={};self.types=set();pin_names=('I0','I1','I3','CIN','COUT','SUM')
        for row in json.loads(path.read_text()):
            key=row['tile_type'],row['slot'],row['mode'];self.types.add(key[0])
            if key in self.bindings or not 0<=key[1]<6 or not 0<=key[2]<9:raise ValueError('Duplicate/invalid native ALU binding')
            if [p['pin_index'] for p in row['pins']]!=list(range(6)):raise ValueError('Incomplete ALU pins')
            if any(p['node'][:2]!=row['site'] or not p['node_name'] for p in row['pins']):raise ValueError('Unsupported ALU pin location')
            self.bindings[key]={name:p['node_name'] for name,p in zip(pin_names,row['pins'])}
        if len(self.types)!=9 or set(self.bindings)!={(t,s,m) for t in self.types for s in range(6) for m in range(9)}:raise ValueError('Incomplete ALU mode/site coverage')
        self.sha256=meta['bindings_sha256']

    def pins(self,tile_type,slot,mode):
        if (tile_type,slot,mode) not in self.bindings:raise ValueError('Unverified ALU type/slot/mode')
        return dict(self.bindings[tile_type,slot,mode])

    def carry_connection(self,source,destination):
        raise ValueError('Dedicated carry ownership and cross-CFU legality are not yet calibrated; ordinary fanout absence is not a disconnection proof')
