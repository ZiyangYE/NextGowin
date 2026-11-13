"""Read Yosys product-port traces; classify failures without claiming reachability."""
import argparse,json
from pathlib import Path


def summarize(path,helpers):
    names={};widths={};values={};frames=[];current=None;dirty=False
    def capture():
        if current is None or not dirty:return
        failures=[]
        for name,gold in sorted(values.items()):
            if not name.startswith('gold_'):continue
            port=name[5:];gate=values.get('gate_'+port)
            if gate is None:continue
            if len(gold)!=len(gate):raise ValueError('Product port width mismatch')
            bad=[i for i,(a,b) in enumerate(zip(gold[::-1],gate[::-1])) if a in '01' and a!=b]
            if not bad:continue
            kind='auxiliary' if port.startswith('ng_state_helper_') else ('memory' if port.startswith('ng_mem_') else 'original_io')
            row=dict(port=port,kind=kind,bits=bad,gold=gold,gate=gate)
            if kind=='auxiliary':row['candidate']=helpers[int(port.removeprefix('ng_state_helper_'))]['name']
            failures.append(row)
        frames.append(dict(frame=current,failures=failures,definedness_violation=values.get('ng_defined_violation')))
    for line in Path(path).read_text().splitlines():
        if line.startswith('$var'):
            p=line.split();names[p[3]]=p[4].lstrip('\\');widths[p[3]]=int(p[2])
        elif line.startswith('#'):
            capture();current=int(line[1:]);dirty=False
        elif line.startswith('b'):
            value,code=line[1:].split();values[names[code]]=value.rjust(widths[code],value[0] if value[0] in 'xz' else '0');dirty=True
        elif line and line[0] in '01xz' and line[1:] in names:
            values[names[line[1:]]]=line[0];dirty=True
    capture()
    return dict(scope='Induction trace unless independently labeled otherwise; no initial-state reachability claim',frames=frames)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--vcd',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    result=summarize(a.vcd,json.loads(a.manifest.read_text())['helpers']);a.out.write_text(json.dumps(result,indent=2))
    print(json.dumps([dict(frame=f['frame'],failures=len(f['failures']),examples=[r.get('candidate',r['port']) for r in f['failures'][:10]],definedness_violation=f['definedness_violation']) for f in result['frames']]))
