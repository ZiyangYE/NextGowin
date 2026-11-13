"""Reuse the user's generated .rao wrapper with a nontrivial user datapath."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from run_abc_reproduction import ROOT, sha


def main():
    source=ROOT/'binary_audit/trace/probe/impl/gwsynthesis/probe.prj'
    tree=ET.parse(source)
    files=tree.find('.//FileList')
    for item in files:
        if item.attrib['type']=='verilog':
            item.set('path',str(ROOT/'examples/cpu_gao/top.v'))
    out=ROOT/'build/cpu_gao_example'
    out.mkdir(parents=True,exist_ok=True)
    target=out/'frontend.prj'
    tree.write(target,encoding='utf-8',xml_declaration=True)
    rao=ROOT/'binary_audit/flow/inputs/probe.rao'
    config=dict(project=target.relative_to(ROOT).as_posix(),sdc='examples/cpu_gao/constraints.sdc',
        rao=rao.relative_to(ROOT).as_posix(),device_config='binary_audit/flow/device.cfg',
        macro_profile='build/cpu_gao_example/macro.json',
        physical_profile='configs/cpu_gao_native.json',
        period_ps=10000,strategy='acd_remap',timeout=600,no_iob_registers=True)
    (out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    files=[source,rao,ROOT/'binary_audit/trace/probe/impl/gwsynthesis/RTL_GAO/gw_gao_top.v']
    compiled=ROOT/'binary_audit/flow/gao/05_mapped_gao.vg'
    verified=json.loads((ROOT/'binary_audit/flow/verification.json').read_text())
    expected=verified['files']['gao/05_mapped_gao.vg']['sha256']
    if sha(compiled)!=expected:raise ValueError('Previously verified GAO macro changed')
    generated=ROOT/'binary_audit/trace/probe/impl/gwsynthesis/RTL_GAO'
    profile=dict(schema='native-gao-macro-v1',netlist=compiled.relative_to(ROOT).as_posix(),
        netlist_sha256=expected,rao_sha256=sha(rao),module='gw_gao',discard_top='top',
        generated_inputs={p.relative_to(ROOT).as_posix():sha(p) for p in generated.rglob('*') if p.is_file()},
        ports=dict(a='a_d',q='q_d',clk='clk_d',tms_pad_i='tms_pad_i',tck_pad_i='tck_pad_i',
                   tdi_pad_i='tdi_pad_i',tdo_pad_o='tdo_pad_o'),
        external_io_ports=['tms_pad_i','tck_pad_i','tdi_pad_i','tdo_pad_o'])
    (out/'macro.json').write_text(json.dumps(profile,indent=2)+'\n')
    (out/'provenance.json').write_text(json.dumps(dict(
        scope='Existing user-generated RTL GAO snapshot: sample a/q on clk. No arbitrary .rao generator.',
        inputs={str(p):sha(p) for p in files}),indent=2)+'\n')
    print('Prepared',out/'config.json')


if __name__=='__main__':main()
