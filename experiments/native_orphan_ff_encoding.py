"""Reversible mixed-CFU encoding after removing an even shared FF/LUT.

The odd paired FF remains at its original slot beside another independently
enabled FF and an unrelated LUT. No vendor P&R or hardware programming.
"""
import argparse,gzip,json,os,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from ownpr_image_storage import sha
from workspace_budget import reserve


def fixture(ce):
    from native_shared_ff_encoding import fixture as pair_fixture
    text=pair_fixture('DFFRE',ce,'zero').replace('output [1:0] q','output [3:0] q')
    extra='''wire f2,f3,q2;
LUT4 l2(.I0(d0),.I1(d1),.I2(ce),.I3(sr),.F(f2));defparam l2.INIT=16'hf0f0;
LUT4 l3(.I0(d0),.I1(d1),.I2(ce),.I3(sr),.F(f3));defparam l3.INIT=16'hff00;
DFFRE r2(.D(f2),.CLK(ck),.CE(sr),.RESET(zero),.Q(q2));
OBUF o2(.I(q2),.O(q[2])); OBUF o3(.I(f3),.O(q[3]));
endmodule'''
    return text.replace('endmodule',extra)


def worker(out,base,ce,site):
    import ctypes as C
    import numpy as np
    from native_device_session import NativeDeviceSession
    from ownpr_device import Device
    from ownpr_lut_configuration import NativeFrameProjection,LutInitChoices
    from ownpr_cls_configuration import ClsChoices
    from ownpr_ff_configuration import single_ff_attributes
    from ownpr_shared_ff_calibration import predict
    from ownpr_clock_images import image_sha,compact
    from audit_native_encoding_probe import payload
    device=Device();r,c=site
    if device.grid[r-1][c-1]!=17:raise ValueError('Mixed orphan fixture requires type17')
    n=NativeDeviceSession(out);vp=C.c_void_p;ptr=n.ptr;fn=n.fn
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(n.mem);vector=(C.c_uint64*3)()
    fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vector))
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp]);objects={}
    for p in range(vector[0],vector[1],8):
        obj=ptr(p);buf=C.create_string_buffer(32);getname(obj,C.addressof(buf));b=C.addressof(buf);length=ptr(b+16)
        if length>64:raise ValueError('Unexpected fixture name')
        name=C.string_at(ptr(b) if ptr(b+24)>=16 else b,length).decode();objects[name]=obj
    grid=n.getgrid(n.chip,r,c);agg=ptr(grid+0x70);groups=[ptr(agg+0x360+8*i) for i in range(4)]
    setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp]);setlogic=fn('?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp])
    bindlut=fn('?setPin2Node@Lut4@NETLIST@@UEAAXXZ',None,[vp]);bindreg=fn('?setPin2Node@Reg@NETLIST@@UEAAXXZ',None,[vp])
    getpin=fn('?getOnePin@Reg@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint]);direct=fn('?isDirectConn@Pin@NETLIST@@QEAA_NPEAVglobalMem@@@Z',C.c_bool,[vp,vp])
    encode=fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[vp]);other=(base+2)%6
    slots={0:base,1:base+1,2:other,3:6};patterns={0:0xAAAA,1:0xCCCC,2:0xF0F0,3:0xFF00};saved={}
    for i,slot in slots.items():
        lut=ptr(agg+8*slot);reg=ptr(agg+0xa0+8*slot)
        saved[i]={'lut':lut,'reg':reg,'word':C.c_ushort.from_address(lut+0x20).value,'unused':C.c_ubyte.from_address(lut+0x22).value,
                  'lut_obj':ptr(lut+8),'ff_obj':ptr(reg+8),'lut_flag':C.c_ubyte.from_address(agg+0x326+slot).value,'ff_flag':C.c_ubyte.from_address(agg+0x318+slot).value}
        setphy(objects[f'l{i}'],lut);setlogic(lut,objects[f'l{i}']);C.c_ushort.from_address(lut+0x20).value=patterns[i]
        C.c_ubyte.from_address(lut+0x22).value=0;C.c_ubyte.from_address(agg+0x326+slot).value=1;bindlut(objects[f'l{i}'])
        if i<3:
            setphy(objects[f'r{i}'],reg);setlogic(reg,objects[f'r{i}']);C.c_ubyte.from_address(agg+0x318+slot).value=1;bindreg(objects[f'r{i}'])
            if not direct(getpin(objects[f'r{i}'],0),n.mem):raise ValueError('Paired FF data is not direct')
    def save(label):
        if encode(n.mem):raise ValueError('Mixed orphan encoder failed')
        raw=out/'probe.fs';path=out/(label+'.fs.gz')
        with raw.open('rb') as src,gzip.open(path,'xb',compresslevel=6) as dst:shutil.copyfileobj(src,dst)
        if image_sha(path)!=sha(raw):raise ValueError('Compressed witness differs')
        return {'file':path.name,'original_sha256':sha(raw),'compressed_sha256':sha(path),'attributes':[list(struct.unpack('<19h',C.string_at(g+6,38))) for g in groups],
                'remaining_paired_data_direct':[bool(direct(getpin(objects[f'r{i}'],0),n.mem)) for i in (1,2)]}
    save('warmup');before=save('paired')
    s=saved[0];setphy(objects['r0'],None);setlogic(s['reg'],s['ff_obj']);C.c_ubyte.from_address(agg+0x318+base).value=s['ff_flag']
    odd_luts=save('orphan_with_even_lut')
    setphy(objects['l0'],None);setlogic(s['lut'],s['lut_obj']);C.c_ushort.from_address(s['lut']+0x20).value=s['word']
    C.c_ubyte.from_address(s['lut']+0x22).value=s['unused'];C.c_ubyte.from_address(agg+0x326+base).value=s['lut_flag']
    odd=save('orphan')
    for kind,physical in [('l',s['lut']),('r',s['reg'])]:setphy(objects[kind+'0'],physical);setlogic(physical,objects[kind+'0'])
    C.c_ushort.from_address(s['lut']+0x20).value=patterns[0];C.c_ubyte.from_address(s['lut']+0x22).value=0
    C.c_ubyte.from_address(agg+0x326+base).value=1;C.c_ubyte.from_address(agg+0x318+base).value=1
    bindlut(objects['l0']);bindreg(objects['r0']);restored=save('restored')
    projection=NativeFrameProjection(device);lut_codec=LutInitChoices(device);cls=ClsChoices(device)
    profile=lambda slot,enable:dict(cell_type='DFFRE',slot=slot,ce=enable,control='zero',clock='signal')
    baseline_attrs=predict([profile(base,ce),profile(base+1,ce)])
    baseline_attrs[other//2]=single_ff_attributes('DFFRE',other,'signal','zero')[other//2]
    odd_attrs=single_ff_attributes('DFFRE',base+1,ce,'zero');odd_attrs[other//2]=baseline_attrs[other//2]
    def requirements(attrs,removed=False):
        bits={}
        # Unused LUTs emit no active-low INIT fuses, regardless of the stale
        # RAM word in an unbound physical object: their encoded INIT is FFFF.
        for i,slot in slots.items():bits.update(lut_codec.requirements(r-1,c-1,slot,0xFFFF if removed and i==0 else patterns[i]))
        for i,v in enumerate(attrs):bits.update(cls.requirements(r-1,c-1,i,v))
        return bits
    records=[];a=projection.read(out/before['file'])
    for label,row,removed in [('orphan_with_even_lut',odd_luts,False),('orphan',odd,True)]:
        b=projection.read(out/row['file']);req=requirements(odd_attrs,removed);actual=set(np.flatnonzero(a!=b).tolist())
        expected={projection.index(bit) for bit,v in req.items() if a[projection.index(bit)]-48!=v}
        mismatches=[list(bit) for bit,v in req.items() if b[projection.index(bit)]-48!=v]
        records.append({'label':label,'actual_changes':len(actual),'expected_changes':len(expected),'unexpected':len(actual-expected),
                        'missing':len(expected-actual),'value_mismatches':mismatches,'attributes_match':row['attributes']==odd_attrs,'direct_data':all(row['remaining_paired_data_direct'])})
    baseline_mismatch=[list(bit) for bit,v in requirements(baseline_attrs).items() if a[projection.index(bit)]-48!=v]
    restored_ok=payload(out/before['file'])==payload(out/restored['file'])
    passed=before['attributes']==baseline_attrs and not baseline_mismatch and restored_ok and all(x['attributes_match'] and x['direct_data'] and not x['unexpected'] and not x['missing'] and not x['value_mismatches'] for x in records)
    report={'status':'mixed_orphan_encoding_matched' if passed else 'failed','site':site,'tile_type':17,'base_slot':base,'ce':ce,
            'profile':profile(base+1,ce),'other_profile':profile(other,'signal'),'source_fse_sha256':device.fse.sha256,
            'baseline':before,'orphan_with_even_lut':odd_luts,'orphan':odd,'restored':restored,'checks':records,
            'baseline_mismatches':baseline_mismatch,'unused_lut_encoded_init':65535,'full_payload_restored':restored_ok,'routing_verified':False,'timing_signoff':False}
    (out/'encoding_audit.json').write_text(json.dumps(report,indent=2)+'\n');compact(out)
    if not passed:raise ValueError('Mixed orphan encoding prediction failed')


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true')
    p.add_argument('--base-slot',type=int,choices=(0,2,4),required=True);p.add_argument('--ce',choices=('one','signal'),required=True)
    p.add_argument('--site',type=int,nargs=2,default=(50,90));a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out,a.base_slot,a.ce,a.site)
    reserve(ROOT,200_000_000);prepare(out);(out/'probe.vg').write_text(fixture(a.ce));cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+'-bit\n')
    shutil.copyfile(__file__,out/'extractor_source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();error=None
    args=[str(python),str(Path(__file__).resolve()),'--worker','--out',str(out),'--base-slot',str(a.base_slot),'--ce',a.ce,'--site',*map(str,a.site)]
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run(args,cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=120).returncode
        except subprocess.TimeoutExpired as e:rc=-1;error=repr(e)
    from capture_runtime_tables import EXPECTED
    report={'status':'captured' if rc==0 else 'failed','returncode':rc,'error':error,'wall_seconds':time.monotonic()-start,'dll_sha256':EXPECTED,
            'vendor_placement_executed':False,'vendor_routing_executed':False,'open_chip_database_used':False,'device_programmed':False,
            'source_sha256':{n:sha(out/n) for n in ('probe.vg','extractor_source.py.txt')}}
    if (out/'encoding_audit.json').exists():report['encoding_audit_sha256']=sha(out/'encoding_audit.json')
    (out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
    if rc:raise SystemExit(1)


if __name__=='__main__':main()
