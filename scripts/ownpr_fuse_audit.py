"""Audit ordinary-switch configuration consistency from native FSE records.

The selected row is compared against its destination mux's union mask. The
polarity of the physical bit is intentionally unspecified: consistency of two
selections is invariant under a common bit inversion. This does not encode
primitive modes, default/alonenode bits, I/O banks or a complete bitstream.
"""
import argparse,json,struct
from collections import defaultdict
from pathlib import Path
from ownpr_device import Device

class SwitchChoices:
    def __init__(self,device):
        self.device=device;self.templates={};self.fuse=device.fse.get_table('header',1)
    def bit(self,typ,index):
        if not 0<index<self.fuse['rows'] or not 0<=typ<self.fuse['columns']:raise ValueError('Invalid native fuse index')
        offset=self.fuse['data_offset']+(index*self.fuse['columns']+typ)*2
        value=struct.unpack_from('<h',self.device.fse.data,offset)[0]
        if value<0:raise ValueError('Undefined fuse location')
        row,col=divmod(value,200);block=self.device.fse.blocks[str(typ)]
        if not 0<=row<block['height'] or not 0<=col<block['width']:raise ValueError('Fuse outside native tile bitmap')
        return row,col
    def template(self,typ):
        if typ not in self.templates:
            mask=defaultdict(set);choices={}
            for row in self.device.routing_connections(typ):
                if row['source_name'] is None or row['destination_name'] is None:continue
                bits={self.bit(typ,i) for i in row['configuration_indices'] if i>=0}
                key=(row['source_name'],row['destination_name'])
                if key in choices and choices[key]!=bits:raise ValueError('Ambiguous native switch encoding')
                choices[key]=bits;mask[key[1]].update(bits)
            self.templates[typ]={key:{b:int(b in bits) for b in mask[key[1]]} for key,bits in choices.items()}
        return self.templates[typ]
    def requirements(self,pip):
        r,c,src,dst=pip;typ=self.device.grid[r][c];values=self.template(typ)[src,dst]
        return {(r,c,br,bc):v for (br,bc),v in values.items()}

def audit(routes,choices):
    values=defaultdict(lambda:defaultdict(set));pips=0
    for name,net in routes['nets'].items():
        for pip in net['pips']:
            pips+=1
            for bit,value in choices.requirements(pip).items():values[bit][value].add(name)
    conflicts=[dict(bit=list(bit),zero=sorted(v[0]),one=sorted(v[1])) for bit,v in values.items() if len(v)>1]
    return dict(status='consistent' if not conflicts else 'conflicting',pips_checked=pips,physical_switch_bits=len(values),conflicts=conflicts,
        primitive_configuration_checked=False,bitstream_validated=False,
        scope='Native ordinary-switch row/mask consistency model only; requires native encoder cross-validation before signoff.')

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--routes',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    r=audit(json.loads(a.routes.read_text()),SwitchChoices(Device()));a.out.write_text(json.dumps(r,indent=2));print({k:v for k,v in r.items() if k!='conflicts'},'conflicts',len(r['conflicts']))
