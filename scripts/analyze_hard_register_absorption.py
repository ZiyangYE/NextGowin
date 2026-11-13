"""Find generic DSP -> optional hold-mux -> FF banks; analysis only.

No RTL rewrite, physical feasibility, or timing gain is implied. Hardware INIT,
clock/CE/reset configuration and full equivalence remain mandatory gates.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path

from ownpr_image_storage import load_json,sha


def analyze(design):
    tops=[(n,m) for n,m in design['modules'].items() if m.get('cells')]
    if len(tops)!=1:raise ValueError('Expected one flattened implementation module')
    top,module=tops[0];cells=module['cells'];loads=defaultdict(list);constants={'0':'0','1':'1'}
    for n,c in cells.items():
        if c['type'] in ('VCC','GND'):
            for bits in c['connections'].values():
                for b in bits:constants[b]='1' if c['type']=='VCC' else '0'
        for p,bits in c['connections'].items():
            if c.get('port_directions',{}).get(p)=='input':
                for b in bits:loads[b].append((n,p))
    for p,port in module.get('ports',{}).items():
        if port['direction'] in ('output','inout'):
            for b in port['bits']:loads[b].append(('$top:'+p,p))
    def bit(b):return constants.get(b,b)
    def scalar(c,p):
        values=c['connections'][p]
        if len(values)!=1:raise ValueError('Non-scalar FF control')
        return bit(values[0])
    banks=[]
    for name,dsp in cells.items():
        if dsp['type']!='MULTALU27X18':continue
        rows=[];rejected=[]
        for index,wire in enumerate(dsp['connections'].get('DOUT',[])):
            sinks=loads[wire]
            if not sinks:continue
            reason=None;intermediate=None;enable=None
            if len(sinks)!=1:reason='DSP bit has multiple consumers'
            else:
                first,port=sinks[0];cell=cells.get(first,{'type':'TOP_PORT'});ffname=first
                if cell['type'].startswith('LUT'):
                    intermediate=first
                    outputs=[b for p,bs in cell['connections'].items() if cell['port_directions'][p]=='output' for b in bs]
                    if len(outputs)!=1 or len(loads[outputs[0]])!=1:reason='LUT output is not private to one FF'
                    else:
                        ffname,port=loads[outputs[0]][0];ff=cells.get(ffname,{'type':'TOP_PORT'})
                        if ff['type']!='DFFRE' or port!='D':reason='No supported synchronous FF after LUT'
                        elif cell['type']=='LUT1' and int(cell['parameters']['INIT'],2)==2 and cell['connections']['I0']==[wire]:pass
                        elif cell['type']=='LUT3' and int(cell['parameters']['INIT'],2)==0xCA and cell['connections']['I1']==[wire] and cell['connections']['I0']==ff['connections']['Q']:
                            enable=scalar(cell,'I2')
                        else:reason='LUT is not the supported identity or feedback mux'
                if reason is None:
                    ff=cells.get(ffname,{'type':'TOP_PORT'})
                    if ff['type']!='DFFRE' or port!='D':reason='Unsupported FF type or input'
                    else:
                        ce=scalar(ff,'CE');factors=[x for x in (ce,enable) if x is not None and x!='1']
                        effective='0' if '0' in factors else sorted(set(factors),key=str)
                        rows.append({'dsp_bit':index,'ff':ffname,'lut':intermediate,'feedback_enable':enable,
                                     'clock':scalar(ff,'CLK'),'reset':scalar(ff,'RESET'),
                                     'ff_enable':ce,'effective_enable_and':effective,'init':ff['parameters'].get('INIT','SDK_DEFAULT')})
            if reason:rejected.append({'dsp_bit':index,'reason':reason})
        groups=defaultdict(list)
        for row in rows:
            key=json.dumps({k:row[k] for k in ('clock','reset','effective_enable_and','init')},sort_keys=True)
            groups[key].append(row['dsp_bit'])
        banks.append({'instance':name,'current_oreg_clock':dsp['parameters'].get('OREG_CLK','SDK_DEFAULT'),
                      'candidate_bits':rows,'rejected_used_bits':rejected,
                      'control_groups':[{'controls':json.loads(k),'bits':v} for k,v in groups.items()],
                      'rewrite_admitted':False})
    return {'top':top,'scope':__doc__,'banks':banks,'candidate_ff_count':sum(len(b['candidate_bits']) for b in banks),
            'candidate_lut_count':sum(r['lut'] is not None for b in banks for r in b['candidate_bits']),
            'required_gates':['SDK initialization relation','DSP clock/CE/reset encoding calibration',
                              'all output/cascade consumer coverage','full source equivalence','own routing and full I/O native STA']}


def main():
    p=argparse.ArgumentParser();p.add_argument('--netlist',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();report=analyze(load_json(a.netlist));report.update(source=str(a.netlist),source_sha256=sha(a.netlist))
    with a.output.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2);f.write('\n')
    print(json.dumps({'banks':len(report['banks']),'candidate_ff_count':report['candidate_ff_count'],
                      'control_group_counts':[len(b['control_groups']) for b in report['banks']],'rewrite_admitted':False}))


if __name__=='__main__':main()
