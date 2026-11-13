"""Explicit project inputs and per-stage backend coverage, without substitution."""
import argparse,json,hashlib
from collections import Counter
from pathlib import Path
from ownpr_ff_configuration import FF_CONTROLS
from ownpr_place import constraints
from ownpr_device import Device
from ownpr_carry_chains import extract as carry_chains


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def inspect(netlist,cst,sdc,top=None):
    design=json.loads(Path(netlist).read_text())
    modules={n:m for n,m in design['modules'].items() if m.get('cells') and not int(m.get('attributes',{}).get('blackbox','0'),2)}
    if top is None:
        if len(modules)!=1:raise ValueError('Specify top for a hierarchical/multiple-module design')
        top=next(iter(modules))
    if top not in modules:raise ValueError('Top module absent or black box')
    m=modules[top];contract=constraints(m,Path(cst).read_text(),Path(sdc).read_text());counts=Counter(c['type'] for c in m['cells'].values())
    device=Device();sites={name:device.pin_site_name(pin) for name,pin in contract['pins'].items()}
    if len(set(sites.values()))!=len(sites):raise ValueError('Package assignments alias one physical I/O')
    primitive_stage={};packing_blockers=[];signoff_blockers=[]
    for typ,count in sorted(counts.items()):
        if typ in {'LUT1','LUT2','LUT3','LUT4','IBUF','OBUF','VCC','GND','GSR'}|set(FF_CONTROLS):
            item=dict(count=count,packing_supported=True,ordinary_endpoints_supported=True)
            if typ in ('DFFCE','DFFPE'):signoff_blockers.append(dict(resource=typ,reason='Recovery/removal timing coverage not yet calibrated'))
        elif typ=='ALU':
            item=dict(count=count,packing_supported=False,ordinary_endpoints_supported=False,pin_binding_calibrated=True,
                reason='Dedicated carry ownership, chain legality and complete ALU configuration not yet supported')
            packing_blockers.append(dict(resource=typ,count=count,reason=item['reason']))
        else:
            item=dict(count=count,packing_supported=False,ordinary_endpoints_supported=False,reason='Native physical model not yet implemented')
            packing_blockers.append(dict(resource=typ,count=count,reason=item['reason']))
        primitive_stage[typ]=item
    return dict(status='project_contract_checked',top=top,source_files=dict(netlist=str(Path(netlist).resolve()),cst=str(Path(cst).resolve()),sdc=str(Path(sdc).resolve())),
        source_sha256=dict(netlist=sha(netlist),cst=sha(cst),sdc=sha(sdc)),cells=sum(counts.values()),ports=len(contract['ports']),
        period_ns=contract['period_ns'],all_ports_fixed=True,io_electrical=contract['properties'],pins=contract['pins'],
        all_nonclock_io_min_max_constrained=True,primitive_stage_coverage=primitive_stage,
        logical_carry_chains=carry_chains(m),
        package_sites=sites,package_pin_legality_checked=True,clock_source_pin=contract['pins'][contract['clock_port']],
        calibrated_clock_entry_available=contract['pins'][contract['clock_port']]=='L22',
        ready_for_initial_packing=not packing_blockers,packing_blockers=packing_blockers,timing_coverage_blockers=signoff_blockers,
        device='GW5AST-138C',open_chip_database_used=False,vendor_pnr_executed=False,
        full_flow_ready=False,export_contract_pending='Native export still binds the initial fixed-I/O/clock calibration profile; this inventory does not claim a generic export backend')


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--netlist',type=Path,required=True);ap.add_argument('--cst',type=Path,required=True);ap.add_argument('--sdc',type=Path,required=True);ap.add_argument('--top');ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    r=inspect(a.netlist,a.cst,a.sdc,a.top);a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(r,indent=2))
    print(json.dumps({k:r[k] for k in ('top','cells','ports','ready_for_initial_packing','packing_blockers','timing_coverage_blockers')},indent=2))
