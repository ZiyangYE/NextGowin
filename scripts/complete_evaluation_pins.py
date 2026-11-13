"""Add only missing evaluation pins using the installed vendor package data.

Existing assignments are immutable. This is not a board wiring prescription.
Only ordinary, non-configuration pins in existing LVCMOS33 banks are eligible.
"""
import argparse,json,re
from pathlib import Path
from ownpr_device import Device,ROOT
from ownpr_place import constraints,sha
from workspace_budget import reserve


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('netlist','cst','sdc','out'):p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--top',default='top');a=p.parse_args()
    module=json.loads(a.netlist.read_text())['modules'][a.top];original=a.cst.read_text();device=Device()
    pins=dict(re.findall(r'IO_LOC\s+"([^"]+)"\s+(\w+)\s*;',original));props=dict(re.findall(r'IO_PORT\s+"([^"]+)"\s+([^;]+);',original))
    ports={name if len(row['bits'])==1 else f'{name}[{i+row.get("offset",0)}]':row['direction'] for name,row in module['ports'].items() for i in range(len(row['bits']))}
    missing=sorted(set(ports)-set(pins));package={row['INDEX']:row for row in device.pins['PIN_DATA']};used=set(pins.values())
    if len(used)!=len(pins):raise ValueError('Existing pin collision')
    banks={package[pin].get('BANK') for pin in used};bad=set()
    for name,pin in pins.items():
        kind=re.search(r'\bIO_TYPE=(\w+)\b',props.get(name,''))
        if not kind or kind[1]!='LVCMOS33':bad.add(package[pin].get('BANK'))
    choices=sorted((r for r in package.values() if r['TYPE']=='I/O' and not r.get('CFG') and r['INDEX'] not in used and r['BANK'] in banks-bad),key=lambda r:r['INDEX'])
    if len(choices)<len(missing):raise ValueError('Not enough compatible unassigned ordinary pins')
    additions=[];result=original.rstrip()+'\n'
    for name,row in zip(missing,choices):
        if ports[name] not in ('input','output'):raise ValueError('Unknown bidirectional electrical contract')
        electrical='IO_TYPE=LVCMOS33 PULL_MODE=NONE'+(' DRIVE=8' if ports[name]=='output' else '')
        result+=f'IO_LOC "{name}" {row["INDEX"]};\nIO_PORT "{name}" {electrical};\n'
        additions.append(dict(port=name,pin=row['INDEX'],bank=row['BANK'],site=device.pin_site_name(row['INDEX']),package_record=row))
    checked=constraints(module,result,a.sdc.read_text())
    if any(checked['pins'][n]!=pin or checked['properties'][n]!=props[n] for n,pin in pins.items()):raise ValueError('An existing assignment changed')
    reserve(ROOT,1_000_000);a.out.mkdir(exist_ok=False);(a.out/'fixed.cst').write_text(result)
    report=dict(status='evaluation_pins_completed',existing_pins_preserved=len(pins),added=additions,full_port_coverage=len(ports),evaluation_only=True,board_wiring_verified=False,
        source_cst_sha256=sha(a.cst),output_cst_sha256=sha(a.out/'fixed.cst'),source_netlist_sha256=sha(a.netlist),source_sdc_sha256=sha(a.sdc),package_data_sha256=sha(ROOT/'runtime_bundle/gowin/IDE/data/device/GW5AST-138C/FCPBGA676A.json'))
    (a.out/'manifest.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))


if __name__=='__main__':main()
