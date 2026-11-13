"""Hierarchy-aware native-pin fanout evidence from Yosys JSON.

Reports potential routing pressure, not actual LW allocation or routability.
Boundary ports are joined, not counted as additional physical sinks.
"""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import re


def analyze(design,top,threshold=64,focus=None):
    modules=design['modules'];parent={};leaves=[];instances=[]
    def find(key):
        root=key
        while parent.get(root,root)!=root:root=parent[root]
        while key!=root:
            nxt=parent.get(key,key);parent[key]=root;key=nxt
        return root
    def key(path,bit):return ('constant',bit) if isinstance(bit,str) else (path,bit)
    def join(a,b):
        a,b=find(a),find(b)
        if a!=b:parent[b]=a
    def opaque(name):
        module=modules.get(name)
        return module is None or any(int(module.get('attributes',{}).get(a,'0'),2) for a in ('blackbox','whitebox'))
    def walk(name,path,stack):
        if name in stack:raise ValueError('Recursive hierarchy')
        instances.append((name,path))
        for cname,cell in modules[name].get('cells',{}).items():
            if cell['type']=='$scopeinfo':continue
            child=path+(cname,)
            if opaque(cell['type']):leaves.append((child,path,cell));continue
            for port,row in modules[cell['type']].get('ports',{}).items():
                bits=cell['connections'].get(port)
                if bits is None and row['direction']=='output':continue
                if bits is None or len(bits)!=len(row['bits']):raise ValueError('Hierarchy port mismatch: '+str(child)+'/'+port)
                for a,b in zip(bits,row['bits']):join(key(path,a),key(child,b))
            walk(cell['type'],child,stack+(name,))
    walk(top,(),())
    stats={};constants=set()
    for _,path in instances:
        for value in ('0','1','x','z'):constants.add(find(key(path,value)))
    def record(net):
        return stats.setdefault(net,dict(sink_pins=0,sink_cells=set(),roles=Counter(),sink_types=Counter(),drivers=[],sample_sinks=[]))
    def role(pin):
        pin=pin.upper()
        if pin in {'CLK','CLKA','CLKB','CLK0','CLK1','CLKIN','FCLK','PCLK','ECLK','WCLK','RCLK'}:return 'clock'
        if pin in {'RESET','RESETN','RESET_N','RST','RSTN','SET','CLEAR','PRESET','LSR','RESETA','RESETB'}:return 'set_reset'
        if pin in {'CE','CEA','CEB','OCE','OCEA','OCEB','WRE','WREA','WREB','EN','ENABLE'}:return 'enable'
        return 'data_logic'
    for child,path,cell in leaves:
        for pin,bits in cell.get('connections',{}).items():
            direction=cell.get('port_directions',{}).get(pin)
            if direction is None:
                direction=modules.get(cell['type'],{}).get('ports',{}).get(pin,{}).get('direction')
            if direction is None:raise ValueError('Unknown leaf pin direction: '+cell['type']+'/'+pin)
            for index,bit in enumerate(bits):
                net=find(key(path,bit));row=record(net)
                endpoint=dict(instance='/'.join(child),type=cell['type'],pin=pin,index=index)
                if direction in ('input','inout'):
                    row['sink_pins']+=1;row['sink_cells'].add(child);row['roles'][role(pin)]+=1;row['sink_types'][cell['type']]+=1
                    if len(row['sample_sinks'])<5:row['sample_sinks'].append(endpoint)
                if direction in ('output','inout') and len(row['drivers'])<5:row['drivers'].append(endpoint)
        if cell['type'] in ('VCC','GND'):
            for bits in cell['connections'].values():
                for bit in bits:constants.add(find(key(path,bit)))
    selected={net:row for net,row in stats.items() if row['sink_pins']>=threshold and net not in constants}
    aliases=defaultdict(list)
    for name,path in instances:
        for n,row in modules[name].get('netnames',{}).items():
            for i,bit in enumerate(row['bits']):
                net=find(key(path,bit))
                if net not in selected or len(aliases[net])>=8:continue
                suffix=f'[{i+row.get("offset",0)}]' if len(row['bits'])!=1 else ''
                alias='/'.join(path+(n+suffix,))
                if focus is None or re.search(focus,alias):aliases[net].append(alias)
    rows=[]
    for net,row in selected.items():
        if focus is not None and not aliases[net]:continue
        row=dict(row);row['sink_cells']=len(row['sink_cells']);row['roles']=dict(row['roles']);row['sink_types']=dict(row['sink_types'])
        row['aliases']=aliases[net];row['net_key']=str(net);rows.append(row)
    rows.sort(key=lambda r:(-r['sink_pins'],r['net_key']))
    return dict(schema='nextgowin-route-fanout-v1',scope='Pre-P&R expanded leaf-pin fanout; not physical LW candidate/allocation counts. Constants excluded; opaque IP internal nets are unobserved.',
        top=top,threshold=threshold,focus=focus,leaf_cells=len(leaves),high_fanout_nets=len(rows),
        high_fanout_nonclock_nets=sum(not r['roles'].get('clock') for r in rows),nets=rows)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('input',type=Path);ap.add_argument('--top',required=True);ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--threshold',type=int,default=64);ap.add_argument('--focus',help='Only report nets with a matching alias regex');args=ap.parse_args()
    if args.threshold<1:ap.error('Threshold must be positive')
    data=args.input.read_bytes();result=analyze(json.loads(data),args.top,args.threshold,args.focus)
    result['input_sha256']=hashlib.sha256(data).hexdigest();args.out.parent.mkdir(parents=True,exist_ok=True)
    if args.out.exists():raise ValueError('Choose a fresh diagnostic output')
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='nets'},indent=2))
    for row in result['nets'][:12]:print(row['sink_pins'],row['roles'],row['aliases'][:2])


if __name__=='__main__':main()
