"""Isolated arithmetic binding/encoding calibration, never a production image."""
import ctypes as C
import gzip,json,shutil,struct
import numpy as np
from ownpr_device import Device
from ownpr_lut_configuration import NativeFrameProjection
from audit_native_encoding_probe import payload


def probe(native,out,objects,ties=None):
    vp=C.c_void_p;fn=native.fn;ptr=native.ptr;device=Device();projection=NativeFrameProjection(device)
    setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp]);setlogic=fn('?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp])
    bind=fn('?setPin2Node@Alu@NETLIST@@UEAAXXZ',None,[vp]);encode=fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])
    for obj in objects.values():setphy(obj,None)
    r,c=50,90;aggregate=ptr(native.getgrid(native.chip,r,c)+0x70);fs=out/'probe.fs'
    def save(label):
        path=out/(label+'.fs.gz')
        with fs.open('rb') as src,gzip.open(path,'wb') as dst:shutil.copyfileobj(src,dst)
        return path.name
    for _ in range(2):
        if encode(native.mem):raise ValueError('ALU baseline encode failed')
    baseline=projection.read(fs);baseline_payload=payload(fs);baseline_file=save('alu_baseline');records=[]
    groups=[ptr(aggregate+0x360+i*8) for i in range(4)]
    saved_cls=[C.string_at(p+6,38) for p in groups]
    saved_luts=[C.string_at(ptr(aggregate+8*i)+0x20,3) for i in range(8)]
    saved_flags=C.string_at(aggregate+0x318,0x48)
    getpin=fn('?getOnePin@Alu@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint]);original_nets=[ptr(getpin(objects['alu_2'],i)+8) for i in range(4)]
    cases=[(s,('signal',)*4) for s in range(6)]
    if ties and set(ties)=={0,1}:cases=[(0,p) for p in ((0,'signal',1,'signal'),(1,'signal',1,0),(0,'signal',0,'signal'),(0,'signal',0,1),(0,0,0,'signal'))]
    if (out/'requested_profiles.json').exists():
        profiles=json.loads((out/'requested_profiles.json').read_text())
        if not ties or set(ties)!={0,1} or not 1<=len(profiles)<=12 or any(len(p)!=4 or any(v not in (0,1,'signal') for v in p) for p in profiles):
            raise ValueError('Invalid bounded ALU profile request')
        if len({tuple(p) for p in profiles})!=len(profiles):raise ValueError('Duplicate ALU profile request')
        cases=[(0,p) for p in profiles]
    for slot,profile in cases:
        phy=ptr(aggregate+0x40+slot*8);obj=objects['alu_2'];setphy(obj,phy);setlogic(phy,obj);bind(obj)
        # Native post-route ALU state also has a typed logical cache at +0x20,
        # separate from the common base-instance association at +8.
        C.c_uint64.from_address(phy+0x20).value=obj
        # Own packing must install the calibrated arithmetic CLS mode, not
        # rely on an unrelated LUT to make the native encoder visit the CFU.
        group=groups[slot//2]
        C.c_short.from_address(group+6).value=10
        C.c_short.from_address(group+6+2*(16+(slot&1))).value=10
        for i,value in enumerate(profile):C.c_uint64.from_address(getpin(obj,i)+8).value=original_nets[i] if value=='signal' else ties[value]
        if encode(native.mem):raise ValueError('ALU encoding failed')
        values=projection.read(fs);indices=np.flatnonzero(values!=baseline);file=save('alu_mode2_case'+str(len(records)))
        record=dict(mode=2,slot=slot,native_site=[r,c],tile_type=device.grid[r-1][c-1],variant=file,
            input_profile=profile,
            changed_bits=len(indices),changes=[dict(index=int(i),before=int(baseline[i])-48,after=int(values[i])-48) for i in indices],
            physical_bytes=C.string_at(phy+0x18,0x30).hex())
        record['cls_attributes']=[list(struct.unpack('<19h',C.string_at(p+6,38))) for p in groups]
        record['lut_words']=[list(struct.unpack('<HB',C.string_at(ptr(aggregate+8*i)+0x20,3))) for i in range(8)]
        setlogic(phy,None);setphy(obj,None);C.c_uint64.from_address(phy+0x20).value=0
        for i,net in enumerate(original_nets):C.c_uint64.from_address(getpin(obj,i)+8).value=net
        for p,raw in zip(groups,saved_cls):C.memmove(p+6,raw,len(raw))
        for i,raw in enumerate(saved_luts):C.memmove(ptr(aggregate+8*i)+0x20,raw,len(raw))
        C.memmove(aggregate+0x318,saved_flags,len(saved_flags))
        if encode(native.mem):raise ValueError('ALU restoration encode failed')
        record['full_payload_restored']=payload(fs)==baseline_payload;records.append(record)
        (out/'alu_encoding_probe.json').write_text(json.dumps(dict(baseline=baseline_file,records=records,scope='Isolated native ALU binding effect; default/pin/configuration semantics require separate verification'),indent=2))
        print('ALU_ENCODING',slot,len(indices),record['full_payload_restored'],flush=True)
        if not record['full_payload_restored']:raise ValueError('ALU encoding did not restore')
