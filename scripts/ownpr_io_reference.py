"""Validate a fixed-pin native I/O calibration for the export adapter."""
import hashlib,json
from pathlib import Path
from ownpr_device import DLL_SHA


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(folder,cst):
    folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text())
    if (meta['status']!='captured' or meta['dll_sha256']!=DLL_SHA or not meta['vendor_placement_executed']
        or not meta['vendor_routing_executed'] or meta['cst_sha256']!=sha(cst) or sha(folder/'project.cst')!=sha(cst)):
        raise ValueError('I/O reference does not match the fixed constraint contract')
    state=json.loads((folder/'io_configuration_state.json').read_text());sites={}
    for row in state['io']:
        site=tuple(row['site'])
        if site in sites or len(row['logical'])!=16 or len(row['physical'])!=16:raise ValueError('Invalid I/O calibration rows')
        if row['logical'][0]!=33 or row['physical'][0]!=33 or row['logical'][10]!=93 or row['physical'][10]!=93:
            raise ValueError('Unsupported calibrated I/O electrical profile')
        sites[site]=row
    if not sites:raise ValueError('Empty I/O calibration')
    return dict(cst_sha256=sha(cst),state_sha256=sha(folder/'io_configuration_state.json'),dll_sha256=DLL_SHA,
        source=str(folder.resolve()),state=state,scope='Fixed-pin LVCMOS33 reference; no placement/routing is reused.')
