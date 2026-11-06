"""Native DLL cell-delay table for the calibrated GW5AST-138 LUT slice.

No open device database or external pin-name table is used. This models the
four-component input-delay query, not a complete primitive/clock STA engine.
"""
import hashlib,json,math
from pathlib import Path
from capture_runtime_tables import EXPECTED

ROOT=Path(__file__).resolve().parents[1]
TM_SHA256='a3558f35685ebc58eefce834e82059ce651b9e2a6cbca2d6668094153ca0707f'


class NativeCellTiming:
    def __init__(self,folder=ROOT/'results/native_wire_delays_009'):
        folder=Path(folder);meta=json.loads((folder/'manifest.json').read_text());path=folder/'native_cell_library.json'
        if (meta.get('status')!='captured' or meta.get('dll_sha256')!=EXPECTED
                or meta.get('timing_file_sha256')!=TM_SHA256 or meta.get('open_chip_database_used') is not False
                or meta.get('own_pnr_result') is not False
                or hashlib.sha256(path.read_bytes()).hexdigest()!=meta.get('cell_library_sha256')):
            raise ValueError('Unverified native cell timing capture')
        self.data=json.loads(path.read_text());self.sha256=meta['cell_library_sha256']
        if self.data['width']!=4 or len(self.data['groups'])!=9 or len(self.data['id_map'])!=13:
            raise ValueError('Unsupported native timing layout')
        if sum(len(g['rows']) for g in self.data['groups'])!=317:raise ValueError('Incomplete native timing rows')
        for g in self.data['groups']:
            if any(len(row)!=12 or not all(math.isfinite(v) for v in row) for row in g['rows']):
                raise ValueError('Invalid native timing values')
            if any(not -1<=i<len(g['rows']) for i in g['pin_ids']):raise ValueError('Invalid native pin-to-row mapping')
        queries=self.data['lut_input_queries'];seen=set()
        for q in queries:
            key=q['input'],q['slot_group'],q['selector']
            if key in seen:raise ValueError('Duplicate LUT input timing query')
            seen.add(key)
            if list(self.lut_input(q['input'],6 if q['slot_group'] else 0,q['selector']))!=q['values']:
                raise ValueError('Native LUT input timing query mismatch')
        if seen!={(p,b,s) for p in range(4) for b in range(2) for s in range(3)}:
            raise ValueError('Incomplete native LUT input timing coverage')

    def lut_input(self,input_index,slot,selector):
        """Return stored [min-rise,min-fall,max-rise,max-fall] components.

        Selector 1 and 2 match slow/setup and fast/hold report samples. The
        table is tied to this exact device/speed-library capture. Other parts
        and broader transition semantics require separate calibration.
        """
        if input_index not in range(4) or slot not in range(8) or selector not in range(3):
            raise ValueError('Unsupported LUT input/slot/selector')
        group=self.data['groups'][self.data['id_map'][0]]
        # Native RVA 0x14537f0; group selection at 0x12d829c for device 70.
        pin=2*input_index+int(slot>=6);row=group['pin_ids'][pin]
        if row<0:raise ValueError('Missing native LUT input row')
        return tuple(group['rows'][row][4*selector:4*selector+4])

    def passthrough(self,node_id,selector,enabled):
        """Compensation for a native implicit LUT buffer at the FF input.

        Own packed explicit LUT cells already carry their cell arc. Do not
        add this compensation again to those LUT-to-FF internal connections.
        """
        if selector not in range(3):raise ValueError('Unsupported selector')
        if not enabled:return (0.,0.,0.,0.)
        if node_id not in range(32):raise ValueError('Uncalibrated LUT buffer input')
        return self.lut_input(node_id%4,node_id//4,selector)
