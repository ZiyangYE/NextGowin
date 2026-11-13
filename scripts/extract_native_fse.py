"""Bounds-checked binary table index of installed GW5AST-138C FSE.

All values come from the local vendor file. Table-shape hypotheses are checked
against complete stream consumption and retained byte offsets. Parsing alone
does not establish the electrical meaning of every table or a full chip model.
"""
import argparse,hashlib,json,struct,sys
from collections import Counter
from pathlib import Path

from gowin_installation import SDK
SOURCE=SDK/'share/device/GW5AST-138C/GW5AST-138C.fse'
EXPECTED='19f859e61df5b8da361330d42d1250bd31d03bcfa2912c3ccfbf983cc4329030'

class Fse:
    def __init__(self,path=SOURCE):
        self.path=Path(path);self.data=self.path.read_bytes();self.sha256=hashlib.sha256(self.data).hexdigest()
        if self.sha256!=EXPECTED:raise ValueError('Native FSE version changed; re-audit format')
        self.pos=0;self.blocks={};self.tables=[];self.magic=self.u32()
        if self.magic!=0x124f4d7:raise ValueError('Unexpected FSE header')
        first=True
        while True:
            offset=self.pos;typ=self.u32()
            if typ==0x9a1d85:break
            key='header' if first else str(typ);first=False
            if key in self.blocks:raise ValueError('Repeated block '+key)
            h,w,count=self.u32(),self.u32(),self.u32()
            if count>256:raise ValueError('Invalid block table count')
            block=dict(type=typ,offset=offset,height=h,width=w,tables=[]);self.blocks[key]=block
            for _ in range(count):
                header=self.pos;kind=self.u32();rows=self.u32();unit=2
                if kind==61:cols=self.u32();unit=4
                elif kind==1:cols=512
                elif kind in (2,0x26,0x30,0x5a,0x5b):cols=9
                elif kind==3:cols=3
                elif kind==4:cols=1
                elif kind in (6,0x45):cols=15
                elif kind in (0x12,0x13,0x3a):cols=17
                # This pinned SDK uses 14 shorts for 0x35/0x36; the block-272
                # records at 0xe30cB8 and following table boundary verify it.
                elif kind in (0x35,0x36):cols=14
                elif kind in (0x17,0x18,0x25,0x28,0x29,0x2a,0x2b,0x2c,0x2d,0x2e,0x2f):cols=28
                elif kind in (*range(7,0x11),0x27,0x31,0x34,0x37,0x39,0x3b,0x3e,0x3f,0x41,0x43,0x46,0x48,0x4a,0x4c,0x4e,0x51,0x53,0x55,0x57,0x5c,0x86,0x87,0x9a):cols=3
                elif kind==0x8b:cols=10
                elif kind in (5,0x11,0x14,0x15,0x16,0x19,0x1a,0x1b,0x1c,0x1d,0x1e,0x1f,0x20,0x21,0x22,0x23,0x24,0x32,0x33,0x38,0x3c,0x40,0x42,0x44,0x47,0x49,0x4b,0x4d,0x4f,0x50,0x52,0x54,0x56,0x58,0x59,0x5d,0x5e,0x5f,0x60,0x61,0x62,0x63,0x64,0x65,0x66,0x67,0x68,0x69,0x6a,0x6b,0x6c,0x6d,0x6e,0x6f,0x70,0x71,0x72,0x73,0x74,0x75,0x76,0x77,0x78,0x79,0x7a,0x7b,0x7c,0x7d,0x7e,0x7f,0x80,0x81,0x82,0x83,0x84,0x85,0x88,0x89,0x8a,0x8c,0x8d,0x8e,0x8f,0x90,0x91,0x92,0x93,0x94,0x95,0x96,0x97,0x98,0x99,0x9b,0x9c,0x9e):cols=14
                else:raise ValueError(f'Unknown native table {kind:#x} at {header:#x}')
                size=rows*cols*unit;self.require(size)
                entry=dict(block=key,type=kind,header_offset=header,data_offset=self.pos,rows=rows,columns=cols,element_bytes=unit,bytes=size)
                block['tables'].append(entry);self.tables.append(entry);self.pos+=size
        if self.pos!=len(self.data):raise ValueError(f'Unconsumed FSE bytes: {len(self.data)-self.pos}')
        grid=self.get_table('header',61);self.grid=[list(row) for row in self.read_rows(grid)]
        if not self.grid or any(len(row)!=len(self.grid[0]) for row in self.grid):raise ValueError('Invalid grid')
        if any(str(t) not in self.blocks for row in self.grid for t in row):raise ValueError('Missing tile type in grid')

    def require(self,n):
        if n<0 or self.pos+n>len(self.data):raise ValueError('Truncated/invalid native table')
    def u32(self):self.require(4);v=struct.unpack_from('<I',self.data,self.pos)[0];self.pos+=4;return v
    def get_table(self,block,kind):
        matches=[t for t in self.blocks[str(block)]['tables'] if t['type']==kind]
        if len(matches)!=1:raise ValueError(f'Expected one table {block}/{kind}')
        return matches[0]
    def read_rows(self,table):
        fmt=struct.Struct('<'+str(table['columns'])+('i' if table['element_bytes']==4 else 'h'))
        start=table['data_offset']
        for i in range(table['rows']):yield fmt.unpack_from(self.data,start+i*fmt.size)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--route-nodes',type=Path,required=True);a=ap.parse_args();a.out.mkdir(exist_ok=False)
    f=Fse();node_record=json.loads((a.route_nodes/'manifest.json').read_text())
    if node_record['status']!='captured' or node_record['open_chip_database_used']:raise ValueError('Expected native runtime node names')
    nodes={int(k):v for k,v in node_record['nodes'].items()};connections=[];unknown=Counter();seen=Counter()
    for t in f.tables:
        if t['type']!=2 or t['block']=='header':continue
        for i,row in enumerate(f.read_rows(t)):
            # Native table 2 is source,destination. Direction checked against
            # DLL node names and LUT input/output roles (F has only outgoing
            # switches; A/B/C/D only incoming on the logic switchbox).
            src,dst=row[:2];seen.update((dst,src))
            if dst not in nodes or src not in nodes:unknown.update((x for x in (dst,src) if x not in nodes))
            connections.append(dict(tile_type=int(t['block']),row=i,file_offset=t['data_offset']+i*t['columns']*t['element_bytes'],destination_id=dst,source_id=src,
                destination_name=nodes.get(dst),source_name=nodes.get(src),raw_configuration_indices=list(row[2:])))
    pinpath=SDK/'data/device/GW5AST-138C/FCPBGA676A.json';pins=json.loads(pinpath.read_text())
    record=dict(status='parsed_not_electrically_validated',source=str(f.path),source_sha256=f.sha256,source_bytes=len(f.data),fully_consumed=True,
        open_chip_database_used=False,grid_rows=len(f.grid),grid_columns=len(f.grid[0]),grid_tile_types=dict(Counter(t for row in f.grid for t in row)),
        blocks=len(f.blocks),table_count=len(f.tables),normal_connection_rows=len(connections),unknown_normal_node_ids=dict(unknown),
        node_capture_sha256=hashlib.sha256((a.route_nodes/'manifest.json').read_bytes()).hexdigest(),pin_source=str(pinpath),pin_source_sha256=hashlib.sha256(pinpath.read_bytes()).hexdigest(),
        scope='Native FSE structure, candidate directed connections and native package pin data. Connection orientation/fuse semantics require runtime cross-check; clock tables not covered by normal node names.')
    for name,data in [('manifest.json',record),('tables.json',f.tables),('grid.json',f.grid),('normal_connections.json',connections),('package_pins.json',pins)]:
        (a.out/name).write_text(json.dumps(data,indent=2))
    print(json.dumps(record,indent=2))
if __name__=='__main__':main()
