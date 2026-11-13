"""Create frontend input metadata from checked-in FPGAFly sources, no vendor synthesis."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from run_abc_reproduction import ROOT,sha


def main():
    project=ROOT/'FPGAFLY_ISCAS2027'
    sources=[project/line.strip() for line in (project/'rtl/files.f').read_text().splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    sources += [project/'ip/ddr3/DDR3_320.v',project/'ip/jtag/gw_jtag.v']
    if any(not p.is_file() for p in sources):raise FileNotFoundError('Project source list is incomplete')
    tree=ET.Element('Project');ET.SubElement(tree,'Version').text='beta'
    ET.SubElement(tree,'Device',dict(id='GW5AST-138C',package='FCPBGA676A',speed='1',partNumber='GW5AST-LV138FPG676AC1/I0'))
    files=ET.SubElement(tree,'FileList')
    for path in sources:ET.SubElement(files,'File',dict(path=str(path.resolve()),type='verilog'))
    options=ET.SubElement(tree,'OptionList')
    for key,value in [('top_module','thefly_top'),('verilog_language','verilog-2001'),('include_path',str(project/'rtl'))]:
        ET.SubElement(options,'Option',dict(type=key,value=value))
    out=ROOT/'build/fpgafly_cpu_project';out.mkdir(parents=True,exist_ok=True)
    ET.indent(tree);ET.ElementTree(tree).write(out/'frontend.prj',encoding='utf-8',xml_declaration=True)
    (out/'sources.json').write_text(json.dumps(dict(project=sha(out/'frontend.prj'),
        files={p.relative_to(ROOT).as_posix():sha(p) for p in sources}),indent=2)+'\n')
    print('Prepared',out/'frontend.prj','from',len(sources),'source files')


if __name__=='__main__':main()
