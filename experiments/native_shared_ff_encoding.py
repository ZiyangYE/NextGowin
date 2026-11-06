"""Reversible encoding of two independent LUT/FF pairs sharing controls."""
import argparse,gzip,json,os,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from ownpr_place import sha
from workspace_budget import reserve


def fixture(cell_type='DFFCE',ce='signal',control='signal'):
    from ownpr_ff_configuration import FF_CONTROLS
    if cell_type not in FF_CONTROLS or ce not in ('one','signal') or control not in ('zero','signal'):raise ValueError('Unsupported fixture profile')
    text='''module top(clk,a,q);
input clk; input [3:0] a; output [1:0] q;
wire ck,d0,d1,ce,sr,f0,f1,q0,q1,one;
IBUF bc(.I(clk),.O(ck)); IBUF bd0(.I(a[0]),.O(d0)); IBUF bd1(.I(a[1]),.O(d1));
IBUF be(.I(a[2]),.O(ce)); IBUF br(.I(a[3]),.O(sr)); VCC vc(.V(one)); GSR gs(.GSRI(one));
LUT4 l0(.I0(d0),.I1(d1),.I2(ce),.I3(sr),.F(f0)); defparam l0.INIT=16'haaaa;
LUT4 l1(.I0(d0),.I1(d1),.I2(ce),.I3(sr),.F(f1)); defparam l1.INIT=16'hcccc;
DFFCE r0(.D(f0),.CLK(ck),.CE(ce),.CLEAR(sr),.Q(q0));
DFFCE r1(.D(f1),.CLK(ck),.CE(ce),.CLEAR(sr),.Q(q1));
OBUF o0(.I(q0),.O(q[0])); OBUF o1(.I(q1),.O(q[1])); endmodule
'''
    text=text.replace('DFFCE ',cell_type+' ').replace('.CLEAR(','.'+FF_CONTROLS[cell_type]+'(')
    if ce=='one':text=text.replace('.CE(ce)','.CE(one)')
    if control=='zero':
        text=text.replace('wire ck,','wire zero; GND gc(.G(zero));\nwire ck,').replace('.'+FF_CONTROLS[cell_type]+'(sr)','.'+FF_CONTROLS[cell_type]+'(zero)')
    return text


def worker(out,cell_type='DFFCE',ce='signal',control='signal',base_slot=0):
    import ctypes as C
    from native_device_session import NativeDeviceSession
    n=NativeDeviceSession(out)
    import numpy as np
    from ownpr_device import Device
    from ownpr_lut_configuration import NativeFrameProjection,LutInitChoices
    from ownpr_cls_configuration import ClsChoices
    from ownpr_ff_configuration import single_ff_attributes
    from ownpr_shared_ff_calibration import predict
    from ownpr_clock_images import image_sha,compact
    from audit_native_encoding_probe import payload
    vp=C.c_void_p;ptr=n.ptr;fn=n.fn
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(n.mem)
    vector=(C.c_uint64*3)();fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vector))
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp]);objects={}
    for p in range(vector[0],vector[1],8):
        obj=ptr(p);buf=C.create_string_buffer(32);getname(obj,C.addressof(buf));b=C.addressof(buf);length=ptr(b+16)
        if length>64:raise ValueError('Unexpected fixture name')
        name=C.string_at(ptr(b) if ptr(b+24)>=16 else b,length).decode();objects[name]=obj
    grid=n.getgrid(n.chip,50,90);agg=ptr(grid+0x70);group=[ptr(agg+0x360+8*i) for i in range(4)]
    setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp]);setlogic=fn('?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp])
    bindlut=fn('?setPin2Node@Lut4@NETLIST@@UEAAXXZ',None,[vp]);bindreg=fn('?setPin2Node@Reg@NETLIST@@UEAAXXZ',None,[vp]);getpin=fn('?getOnePin@Reg@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint])
    direct=fn('?isDirectConn@Pin@NETLIST@@QEAA_NPEAVglobalMem@@@Z',C.c_bool,[vp,vp]);encode=fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])
    def bind(index):
        slot=base_slot+index
        lut=ptr(agg+8*slot);reg=ptr(agg+0xa0+8*slot)
        saved=dict(lut=lut,reg=reg,word=C.c_ushort.from_address(lut+0x20).value,unused=C.c_ubyte.from_address(lut+0x22).value,
            lut_flag=C.c_ubyte.from_address(agg+0x326+slot).value,ff_flag=C.c_ubyte.from_address(agg+0x318+slot).value,lut_obj=ptr(lut+8),ff_obj=ptr(reg+8))
        for name,physical in ((f'l{index}',lut),(f'r{index}',reg)):setphy(objects[name],physical);setlogic(physical,objects[name])
        C.c_ushort.from_address(lut+0x20).value=(0xAAAA,0xCCCC)[index];C.c_ubyte.from_address(lut+0x22).value=0
        C.c_ubyte.from_address(agg+0x326+slot).value=1;C.c_ubyte.from_address(agg+0x318+slot).value=1
        bindlut(objects[f'l{index}']);bindreg(objects[f'r{index}'])
        if not direct(getpin(objects[f'r{index}'],0),n.mem):raise ValueError('Paired FF data is not direct')
        return saved
    def save(label):
        if encode(n.mem):raise ValueError('Shared FF encoder failed')
        raw=out/'probe.fs';path=out/(label+'.fs.gz')
        with raw.open('rb') as source,gzip.open(path,'xb',compresslevel=6) as target:shutil.copyfileobj(source,target)
        if image_sha(path)!=sha(raw):raise ValueError('Compressed shared FF witness differs')
        return dict(file=path.name,original_sha256=sha(raw),compressed_sha256=sha(path),attributes=[list(struct.unpack('<19h',C.string_at(p+6,38))) for p in group])
    bind(0);save('warmup');baseline=save('baseline');saved=bind(1);variant=save('variant')
    for name,physical,obj in (('l1',saved['lut'],saved['lut_obj']),('r1',saved['reg'],saved['ff_obj'])):setphy(objects[name],None);setlogic(physical,obj)
    C.c_ushort.from_address(saved['lut']+0x20).value=saved['word'];C.c_ubyte.from_address(saved['lut']+0x22).value=saved['unused']
    C.c_ubyte.from_address(agg+0x326+base_slot+1).value=saved['lut_flag'];C.c_ubyte.from_address(agg+0x318+base_slot+1).value=saved['ff_flag']
    restored=save('restored');device=Device();projection=NativeFrameProjection(device);lut_codec=LutInitChoices(device);cls=ClsChoices(device)
    profile=lambda s:dict(cell_type=cell_type,slot=s,ce=ce,control=control,clock='signal')
    before_groups=single_ff_attributes(cell_type,base_slot,ce,control);after_groups=predict([profile(base_slot),profile(base_slot+1)])
    def requirements(slots,attrs):
        bits={}
        for s in slots:bits.update(lut_codec.requirements(49,89,s,(0xAAAA,0xCCCC)[s-base_slot]))
        for i,v in enumerate(attrs):bits.update(cls.requirements(49,89,i,v))
        return bits
    a=projection.read(out/baseline['file']);b=projection.read(out/variant['file']);before=requirements([base_slot],before_groups);after=requirements([base_slot,base_slot+1],after_groups)
    actual=set(np.flatnonzero(a!=b).tolist());expected={projection.index(bit) for bit,v in after.items() if a[projection.index(bit)]-48!=v}
    mismatches=[dict(bit=list(bit),expected=v,actual=int(b[projection.index(bit)])-48) for bit,v in after.items() if b[projection.index(bit)]-48!=v]
    before_mismatch=[bit for bit,v in before.items() if a[projection.index(bit)]-48!=v]
    recovered=payload(out/baseline['file'])==payload(out/restored['file'])
    attrs_match=baseline['attributes']==before_groups and variant['attributes']==after_groups
    result=dict(status='shared_pair_configuration_matched' if recovered and attrs_match and not before_mismatch and not mismatches and actual==expected else 'failed',
        native_site=[50,90],tile_type=device.grid[49][89],slots=[base_slot,base_slot+1],cell_type=cell_type,profile=profile(base_slot),baseline=baseline,variant=variant,restored=restored,
        full_payload_restored=recovered,attributes_matched=attrs_match,required_bits=len(after),actual_changes=len(actual),expected_changes=len(expected),
        missing=len(expected-actual),unexpected=len(actual-expected),value_mismatches=mismatches,baseline_mismatches=before_mismatch,
        source_fse_sha256=device.fse.sha256,production_packer_enabled=False,physical_legality_verified=False,timing_signoff=False,
        scope='Two distinct paired LUT/FF objects with identical specified controls. Reversible encoding only; no routing, STA or hardware proof.')
    (out/'encoding_audit.json').write_text(json.dumps(result,indent=2));compact(out)
    print(result['status'],result['required_bits'],result['actual_changes'],result['unexpected'],flush=True)
    if result['status']=='failed':raise ValueError('Shared FF encoding prediction failed')


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true')
    p.add_argument('--cell-type',choices=('DFFRE','DFFSE','DFFCE','DFFPE'),default='DFFCE');p.add_argument('--ce',choices=('one','signal'),default='signal');p.add_argument('--control',choices=('zero','signal'),default='signal');p.add_argument('--base-slot',type=int,choices=(0,2,4),default=0);a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out,a.cell_type,a.ce,a.control,a.base_slot)
    reserve(ROOT,200_000_000);prepare(out);(out/'probe.vg').write_text(fixture(a.cell_type,a.ce,a.control));cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+'-bit\n');shutil.copyfile(__file__,out/'extractor_source.py.txt')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic()
    with (out/'worker.log').open('w') as log:
        rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out),'--cell-type',a.cell_type,'--ce',a.ce,'--control',a.control,'--base-slot',str(a.base_slot)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=180).returncode
    from capture_runtime_tables import EXPECTED
    result=dict(status='captured' if rc==0 else 'failed',returncode=rc,wall_seconds=time.monotonic()-start,dll_sha256=EXPECTED,
        vendor_placement_executed=False,vendor_routing_executed=False,open_chip_database_used=False,device_programmed=False,
        source_sha256={n:sha(out/n) for n in ('probe.vg','extractor_source.py.txt')})
    if (out/'encoding_audit.json').exists():result['encoding_audit_sha256']=sha(out/'encoding_audit.json')
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if rc:raise SystemExit(rc)


if __name__=='__main__':main()
