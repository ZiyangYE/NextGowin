"""Rebind an official generated P&R command without losing non-path options."""
import re,shutil
from pathlib import Path
from ownpr_image_storage import sha
from gowin_probe import DEVICE,PART


def rebind(template,out,netlist,cst,sdc,policy,bitstream):
    template,out=Path(template),Path(out)
    source=template.read_text();rows=[];seen=set();original={}
    allowed={'-d','-p','-pn','-cst','-cfg','-sdc','-bit','-tr','-ph','-o','-init_all','-timing',
        '-ireg_not_in_iob','-oreg_not_in_iob','-ioreg_not_in_iob','-cst_error',
        '-convert_sdp32_36_to_sdp16_18','-place_option','-route_option','-retiming_resource',
        '-clock_route_order','-correct_hold','-route_maxfan','-global_freq'}
    inputs={'-d':Path(netlist),'-cst':Path(cst),'-sdc':Path(sdc)}
    targets={'-d':out/'probe.vg','-cst':out/'project.cst','-sdc':out/'probe.sdc'}
    sources={str(template.resolve()):sha(template)}
    for line in source.splitlines():
        if not line.strip():continue
        flag,_,value=line.strip().partition(' ');value=value.strip()
        if flag not in allowed or flag in seen:raise ValueError('Unknown/duplicate official command option: '+flag)
        seen.add(flag);original[flag]=value
        if flag in inputs:
            path=Path(value.strip('"'))
            if sha(path)!=sha(inputs[flag]):raise ValueError('Official command input differs: '+flag)
            sources[str(path.resolve())]=sha(path);value=targets[flag].as_posix()
        elif flag=='-cfg':
            path=Path(value.strip('"'));sources[str(path.resolve())]=sha(path)
            shutil.copyfile(path,out/'device.cfg');value=(out/'device.cfg').as_posix()
        elif flag=='-o' and value:value=(out/'probe.vo').as_posix()
        rows.append(flag+(' '+value if value else ''))
    if not {'-d','-p','-pn','-sdc','-cst','-cfg','-timing','-tr'}<=seen:raise ValueError('Incomplete official P&R command')
    if original['-p']!=DEVICE or original['-pn']!=PART:raise ValueError('Official device/part changed')
    expected={'native_defaults':set(),'core_inputs':{'-ireg_not_in_iob','-ioreg_not_in_iob'},
        'core_all':{'-ireg_not_in_iob','-oreg_not_in_iob','-ioreg_not_in_iob'}}[policy]
    if seen&{'-ireg_not_in_iob','-oreg_not_in_iob','-ioreg_not_in_iob'}!=expected:raise ValueError('Official I/O register policy differs')
    if ('-bit' in seen)!=bool(bitstream):raise ValueError('Official bitstream request differs')
    if '-o' not in seen:raise ValueError('Official post-P&R logic output needed for audit')
    return '\n'.join(rows)+'\n',dict(source_sha256=sources,original_options=original,
        preserved_nonpath_options={k:v for k,v in original.items() if k not in ('-d','-cst','-sdc','-cfg','-o')},
        path_rebinding_only=True,source_device_config_sha256=sources[str(Path(original['-cfg'].strip('"')).resolve())])
