"""Match captured output-register bindings, logical controls and encoded attributes."""
import argparse,json,re
from pathlib import Path
from ownpr_image_storage import load_json,sha
from ownpr_device import Device,ROOT
from ownpr_ioreg_configuration import output_attributes,output_binding,IoregChoices
from ownpr_ioreg_macros import calibration
from ownpr_lut_configuration import NativeFrameProjection
from workspace_budget import reserve

CONTROLS={'RESET':'DFFRE','SET':'DFFSE','CLEAR':'DFFCE','PRESET':'DFFPE'}

def expected_attributes(reg):
    pins={p['pin'].rsplit('.',1)[-1]:p for p in reg['pins']}
    controls=set(pins)&set(CONTROLS)
    if len(pins)!=len(reg['pins']) or len(controls)!=1:raise ValueError('Ambiguous register pins/control')
    control=next(iter(controls));typ=CONTROLS[control]
    if set(pins)!={'D','Q','CLK','CE',control}:raise ValueError('Unsupported output-register interface')
    initial=re.fullmatch(r"1'[bBhH]([01])",reg['initial_value'])
    if not initial or int(initial[1])!=int(typ in ('DFFSE','DFFPE')):raise ValueError('Uncalibrated output-register initial state')
    if any(pins[p].get('inversion_annotation') for p in ('D','Q','CLK')):raise ValueError('Uncalibrated data/clock pin annotation')
    def source(pin):
        cls=pin['source_class'];inv=pin['inversion_annotation']
        if cls in ('.?AVGnd@NETLIST@@','.?AVVcc@NETLIST@@'):
            return 'one' if (cls=='.?AVVcc@NETLIST@@')^inv else 'zero'
        if cls is None or cls=='.?AVInv@NETLIST@@':raise ValueError('Unbound/unresolved control source')
        return 'inverted' if inv else 'signal'
    ce,sr=source(pins['CE']),source(pins[control]);clock='inverted' if reg['inverted_clock'] else 'signal'
    return output_attributes(typ,ce,sr,clock),dict(primitive=typ,control_pin=control,ce=ce,control=sr,clock=clock,initial=int(initial[1]))

def audit(folder):
    evidence=calibration();state=load_json(folder/'ioreg_state.json');reload=load_json(folder/'reload_result.json')
    if reload.get('ioreg_state_sha256')!=sha(folder/'ioreg_state.json') or reload['status']!='fixed_layout_sta_calibrated':raise ValueError('I/O observation lacks unchanged-state calibration')
    device=Device();projection=NativeFrameProjection(device);image=projection.read(folder/'probe.fs');choices=IoregChoices(device)
    regs=[r for r in state['registers_and_iologic'] if r['logical_class']=='.?AVReg@NETLIST@@' and r['physical_header'] and r['physical_header'][0]==0]
    byname={r['instance']:r for r in regs};edges={r['source']:r for r in state['output_register_edges']}
    if len(byname)!=len(regs) or len(edges)!=len(state['output_register_edges']) or set(edges)!=set(byname):raise ValueError('Incomplete or shared output-register edge coverage')
    rows=[];seen=set();encoded={}
    for block in state['iob_blocks']:
        for logic in block['iologic']:
            if not logic['reg_refs']:continue
            refs=logic['reg_refs']
            if logic['block_offset']!=0x28 or len(refs)!=1 or refs[0]['offset']!=0x818:raise ValueError('Uncalibrated I/O logic sharing')
            name=refs[0]['instance']
            if name in seen or name not in byname:raise ValueError('Ambiguous physical output register')
            seen.add(name);reg=byname[name];site=block['site'];edge=edges[name];binding=output_binding(site)
            if reg['physical_header']!=[0,site[0],site[1],1] or not reg['physical_base_logic_matches'] or not reg['typed_and_base_match']:raise ValueError('Register binding/cache mismatch')
            expected,semantics=expected_attributes(reg)
            if expected!=logic['attributes']:raise ValueError('Logical controls differ from native I/O attributes: '+name)
            for pin in reg['pins']:
                label=pin['pin'].rsplit('.',1)[-1];key='SR' if label==semantics['control_pin'] else label;point=binding[key]
                if pin['sta_nodes']!=([] if point is None else [point]) or pin['raw_node']!=point:raise ValueError('I/O-register pin binding changed')
            if (edge['destination']!=block['iobuf'] or edge['source_site']!=reg['physical_header'] or edge['destination_site']!=[0,*site] or not edge['timing_edge_present'] or not edge['source_has_timing_node'] or not edge['destination_has_timing_node'] or edge['minimum_ns']!=0 or edge['maximum_ns']!=0):raise ValueError('Dedicated registered-output link changed')
            values=choices.observed_requirements(site,expected)
            for bit,value in values.items():
                if bit in encoded and encoded[bit]!=value:raise ValueError('Conflicting I/O configuration requirements')
                encoded[bit]=value
                if int(image[projection.index(bit)])-48!=value:raise ValueError('Encoded I/O register mode differs')
            constant_pins=[p for p,v in [('CE',semantics['ce']),(semantics['control_pin'],semantics['control'])] if v in ('zero','one')]
            tile_type=device.grid[binding['D'][0]-1][binding['D'][1]-1]
            rows.append(dict(instance=name,iobuf=block['iobuf'],site=site,semantics=semantics,constant_control_pins=constant_pins,encoded_bits=len(values),direct_output_delay_ns=0.,tile_type=tile_type,placement_mode_admitted_by_existing_backend=tile_type in (49,246,247,248)))
    if seen!=set(byname) or not seen:raise ValueError('Unobserved/empty I/O-register inventory')
    return dict(status='output_ioreg_bindings_controls_and_encoded_bits_verified',registers=rows,physical_output_registers=len(rows),encoded_bits=len(encoded),constant_control_links=sum(len(r['constant_control_pins']) for r in rows),
        source_sha256={str(p):sha(p) for p in (folder/'ioreg_state.json',folder/'physical_before.json',folder/'probe.fs',folder/'reload_result.json')},
        scope='Exclusive output I/O registers in calibrated modes. Dynamic input routes, dedicated clock networks and remaining core/carry rules are separate gates; no full baseline acceptance.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args();reserve(ROOT,2000000);r=audit(a.folder)
    with a.out.open('x') as f:json.dump(r,f,indent=2)
    print(r['status'],r['physical_output_registers'],r['encoded_bits'],r['constant_control_links'])
