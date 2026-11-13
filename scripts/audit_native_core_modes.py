"""Logical-to-physical core register modes and LUT input truth correspondence."""
import argparse,collections,json,re,struct
from pathlib import Path
from ownpr_image_storage import load_json,sha
from ownpr_ff_configuration import single_ff_attributes
from audit_native_ioreg_contract import expected_attributes
from ownpr_device import ROOT


def lut_truth(word,mapping):
    """Map logical INIT into physical A/B/C/D truth, rejecting lost support."""
    if not 0<=word<=0xffff:raise ValueError('Invalid LUT truth')
    active={i for i in range(4) if any(((word>>a)^(word>>(a^(1<<i))))&1 for a in range(16))}
    if not active<=mapping.keys():raise ValueError('Dependent logical LUT input lacks physical/constant binding')
    result=0
    for a in range(16):
        logical=0
        for pin,(source,inverted) in mapping.items():
            value=int(source[1:]) if isinstance(source,str) and source in ('c0','c1') else (a>>source)&1
            logical|=(value^int(inverted))<<pin
        result|=((word>>logical)&1)<<a
    return result


def audit(folder,selector_evidence):
    if selector_evidence['status']!='independent_selector_and_mode_queries_verified' or selector_evidence['cases']!=80:raise ValueError('Missing independent mode query coverage')
    for p,h in selector_evidence['source_sha256'].items():
        if sha(Path(p))!=h:raise ValueError('Independent mode source changed')
    snap=load_json(folder/'physical_before.json');bindings=load_json(folder/'primitive_bindings.json');state=load_json(folder/'ioreg_state.json');pins=snap['logical_pin_nodes'];nodes=snap['routed_endpoint_parent_nodes'];geometry=snap['geometry'];byowner=collections.defaultdict(list)
    reload=load_json(folder/'reload_result.json')
    if reload.get('ioreg_state_sha256')!=sha(folder/'ioreg_state.json') or reload['status']!='fixed_layout_sta_calibrated':raise ValueError('Core state lacks unchanged native calibration')
    for pin in pins.values():byowner[pin['owner']].append(pin)
    def header(g):return list(struct.unpack('<4h',bytes.fromhex(g['physical_header'])))
    owners={(g['name'],tuple(header(g))):int(o) for o,g in geometry.items() if g.get('physical_header')}
    bysite={(b['name'],tuple(b['physical_header'])):b for b in bindings if b.get('physical_header')}
    lut_checks=[];unresolved=[]
    for b in bindings:
        if b['logical_class']!='.?AVLut4@NETLIST@@':continue
        h=b['physical_header'];owner=owners.get((b['name'],tuple(h)));mapping={}
        try:
            if owner is None:raise ValueError('Missing native LUT owner')
            for pin in byowner[owner]:
                label=pin['label'].rsplit('.',1)[-1]
                if label=='F':
                    if pin['inverted']:raise ValueError('Uncalibrated output inversion')
                    continue
                if label not in ('I0','I1','I2','I3'):raise ValueError('Unsupported LUT pin')
                index=int(label[-1]);points=[list(struct.unpack('<3h',bytes.fromhex(nodes[str(n)][0]))) for n in pin['nodes']]
                if len(points)==1 and points[0][:2]==h[1:3] and points[0][2] in range(h[3]*4,h[3]*4+4):source=points[0][2]-4*h[3]
                elif not points:
                    driver=snap['logical_net_connections'][str(pin['net'])][0];cls=pins[str(driver)]['logical_class']
                    if cls not in ('.?AVGnd@NETLIST@@','.?AVVcc@NETLIST@@'):raise ValueError('LUT input has no physical node or constant rail')
                    source='c'+str(int(cls=='.?AVVcc@NETLIST@@'))
                else:raise ValueError('LUT input maps outside its physical A/B/C/D ports')
                if index in mapping:raise ValueError('Duplicate logical LUT input')
                mapping[index]=(source,pin['inverted'])
            literal=re.fullmatch(r"(1|2|4|8|16)'[hH]([0-9a-fA-F]+)",b['init_value'])
            if not literal:raise ValueError('Unsupported native LUT INIT literal')
            width=int(literal[1]);small=int(literal[2],16)
            if small>=1<<width:raise ValueError('LUT INIT exceeds declared width')
            word=sum(((small>>(a%width))&1)<<a for a in range(16));predicted=lut_truth(word,mapping)
            if predicted!=b['physical_init_word']:raise ValueError(f'LUT truth mismatch: {predicted:04x} != {b["physical_init_word"]:04x}')
            lut_checks.append(dict(name=b['name'],site=h,physical_truth=predicted,mapping=mapping))
        except (ValueError,KeyError) as e:unresolved.append(dict(name=b['name'],site=h,kind='lut',reason=str(e)))
    core=[];groups={}
    for reg in state['registers_and_iologic']:
        h=reg.get('physical_header')
        if reg['logical_class']!='.?AVReg@NETLIST@@' or not h or h[0]!=50:continue
        b=bysite[reg['instance'],tuple(h)];slot=h[3];d=next(p for p in reg['pins'] if p['pin'].endswith('.D'));companion=b['ff_companion_lut'];mode=None
        if not reg['physical_base_logic_matches'] or b['cfu_register_used_flags'][slot]!=1:raise ValueError('Core register physical ownership is inactive')
        if d['inversion_annotation']:unresolved.append(dict(name=reg['instance'],site=h,kind='ff',reason='Uncalibrated D inversion'));continue
        if d['sta_nodes']==[[h[1],h[2],133+slot]] and companion['input_mode']==0:
            live=[x for x in bindings if x['logical_class']=='.?AVLut4@NETLIST@@' and x.get('physical_header')==[24,*h[1:]]]
            if len(live)==1 and b['cfu_lut_used_flags'][slot]==1 and d['source_site']!=live[0]['physical_header']:mode='independent_selector'
        elif not d['sta_nodes'] and d['source_class']=='.?AVLut4@NETLIST@@' and d['source_site']==[24,*h[1:]] and companion['input_mode']==0:mode='paired_lut'
        elif d['sta_nodes']==[[h[1],h[2],4*slot+3]] and companion['input_mode']==1 and companion['init_word']==0xff00:mode='identity_lut_adapter'
        if mode is None:unresolved.append(dict(name=reg['instance'],site=h,kind='ff',reason='Unrecognized core data mode',data=d));continue
        _,s=expected_attributes(reg);predicted=single_ff_attributes(s['primitive'],slot,s['ce'],s['control'],s['clock'])[slot//2]
        if mode=='independent_selector':predicted[11+(slot&1)]=1
        key=tuple(h[1:3])+tuple([slot//2]);observed=next(g['attributes'] for g in b['cfu_cls_attributes'] if g['group']==slot//2)
        reg_owner=owners.get((reg['instance'],tuple(h)))
        if reg_owner is None:raise ValueError('Missing physical core register owner')
        control_nets={('SR' if pin['label'].rsplit('.',1)[-1]==s['control_pin'] else pin['label'].rsplit('.',1)[-1]):pin['net'] for pin in byowner[reg_owner] if pin['label'].rsplit('.',1)[-1] in ('CLK','CE',s['control_pin'])}
        if set(control_nets)!={'CLK','CE','SR'}:raise ValueError('Missing core control net identity')
        entry=groups.setdefault(key,dict(predicted=predicted.copy(),observed=observed,slots=[],profiles=[],control_nets=control_nets))
        if entry['control_nets']!=control_nets:raise ValueError('Shared physical controls have different logical nets')
        if observed!=entry['observed'] or slot in entry['slots']:raise ValueError('Ambiguous shared group')
        if entry['profiles'] and any(entry['predicted'][i]!=predicted[i] for i in [*range(11),*range(15,19)]):
            unresolved.append(dict(name=reg['instance'],site=h,kind='ff_group',reason='Shared control configuration differs'))
        entry['profiles'].append(s);entry['slots'].append(slot)
        for i in (11+(slot&1),13+(slot&1)):entry['predicted'][i]=predicted[i]
        core.append(dict(name=reg['instance'],site=h,mode=mode,group=key))
    for key,entry in groups.items():
        if entry['predicted']!=entry['observed']:unresolved.append(dict(kind='ff_group',site=key,reason='Full CLS vector mismatch',fields=[dict(field=i,predicted=a,observed=b) for i,(a,b) in enumerate(zip(entry['predicted'],entry['observed'])) if a!=b]))
    lut_group_coverage=[];uncovered_lut_groups=set();checked_groups=set()
    for b in bindings:
        if b['logical_class']!='.?AVLut4@NETLIST@@':continue
        h=b['physical_header'];key=tuple(h[1:3])+tuple([h[3]//2]);raw=b.get('cfu_cls_attributes')
        if not raw:uncovered_lut_groups.add(key);continue
        if b.get('physical_input_mode')!=0:unresolved.append(dict(kind='lut_mode',name=b['name'],site=h,reason='Logical LUT is not in ordinary truth mode'))
        if key in checked_groups:continue
        checked_groups.add(key);observed=next(g['attributes'] for g in raw if g['group']==h[3]//2)
        expected=groups[key]['predicted'] if key in groups else [9,0,15,5,1,2,0,2,0,2,0,2,2,17,17,21 if key[2]==0 else 0,0,0,0]
        if observed!=expected:unresolved.append(dict(kind='lut_group',site=key,reason='Used LUT-group mode differs',expected=expected,observed=observed))
        lut_group_coverage.append(dict(site=key,attributes=observed))
    return dict(status='captured_core_modes_and_lut_truth_correspond' if not unresolved else 'partial_core_mode_correspondence',registers=len(core),register_modes=dict(collections.Counter(r['mode'] for r in core)),groups=len(groups),logical_luts=len(lut_checks),unresolved=unresolved,uncovered_lut_mode_groups=sorted(uncovered_lut_groups),checked_lut_mode_groups=lut_group_coverage,register_records=core,lut_records=lut_checks,source_sha256={str(p):sha(p) for p in (folder/'physical_before.json',folder/'primitive_bindings.json',folder/'ioreg_state.json')},scope='Captured core register data/control mode vectors and logical-to-physical LUT truth. Missing LUT-group captures remain explicit. Encoded bits, routing, timing and complete baseline acceptance remain separate gates.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('--selectors',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from workspace_budget import reserve
    reserve(ROOT,2000000);r=audit(a.folder,load_json(a.selectors));r['selector_evidence_sha256']=sha(a.selectors)
    with a.out.open('x') as f:json.dump(r,f,indent=2)
    print(r['status'],r['registers'],r['logical_luts'],len(r['unresolved']))
