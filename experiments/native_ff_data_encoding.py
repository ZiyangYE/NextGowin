"""Compare FF-only data configuration with explicit I0/I3 identity LUTs."""
import argparse,gzip,json,os,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from ownpr_image_storage import sha
from workspace_budget import reserve


FIXTURE='''module top(clk,d,q); input clk,d; output [2:0] q;
wire ck,x,one,zero,a,z,f;
IBUF bc(.I(clk),.O(ck)); IBUF bd(.I(d),.O(x)); VCC vc(.V(one)); GND gc(.G(zero)); GSR gs(.GSRI(one));
LUT4 pa(.I0(x),.I1(zero),.I2(zero),.I3(zero),.F(a)); defparam pa.INIT=16'hAAAA;
LUT4 pd(.I0(zero),.I1(zero),.I2(zero),.I3(x),.F(z)); defparam pd.INIT=16'hFF00;
DFFRE reg0(.D(x),.CLK(ck),.CE(one),.RESET(zero),.Q(f));
OBUF o0(.I(f),.O(q[0])); OBUF o1(.I(a),.O(q[1])); OBUF o2(.I(z),.O(q[2])); endmodule
'''


def worker(out):
    from native_device_session import NativeDeviceSession
    n=NativeDeviceSession(out)
    import ctypes as C
    from ownpr_device import Device
    from ownpr_lut_configuration import NativeFrameProjection,LutInitChoices
    from ownpr_clock_images import image_sha,compact
    from audit_native_encoding_probe import payload
    vp=C.c_void_p;ptr=n.ptr;fn=n.fn;objects={};design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(n.mem);vector=(C.c_uint64*3)()
    fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(vector))
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    for p in range(vector[0],vector[1],8):
        obj=ptr(p);buf=C.create_string_buffer(32);getname(obj,C.addressof(buf));b=C.addressof(buf);length=ptr(b+16)
        if length>64:raise ValueError('Unexpected fixture instance name')
        objects[C.string_at(ptr(b) if ptr(b+24)>=16 else b,length).decode()]=obj
    agg=ptr(n.getgrid(n.chip,50,90)+0x70);lut=ptr(agg);reg=ptr(agg+0xa0);groups=[ptr(agg+0x360+8*i) for i in range(4)]
    setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp]);setlogic=fn('?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp])
    bindlut=fn('?setPin2Node@Lut4@NETLIST@@UEAAXXZ',None,[vp]);bindreg=fn('?setPin2Node@Reg@NETLIST@@UEAAXXZ',None,[vp]);getreg=fn('?getOnePin@Reg@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint]);getlut=fn('?getOnePin@Lut4@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint]);encode=fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])
    data=getreg(objects['reg0'],0);original_net=ptr(data+8);setphy(objects['reg0'],reg);setlogic(reg,objects['reg0']);C.c_ubyte.from_address(agg+0x318).value=1
    device=Device();projection=NativeFrameProjection(device);codec=LutInitChoices(device);records=[]
    def save(label,driver,flag,standalone_word=0):
        for name in ('pa','pd'):setphy(objects[name],None)
        setlogic(lut,None);C.c_ushort.from_address(lut+0x20).value=standalone_word;C.c_ubyte.from_address(lut+0x22).value=1;C.c_ubyte.from_address(agg+0x326).value=flag;C.c_uint64.from_address(data+8).value=original_net
        if driver:
            setphy(objects[driver],lut);setlogic(lut,objects[driver]);C.c_ushort.from_address(lut+0x20).value=0xAAAA if driver=='pa' else 0xFF00;C.c_ubyte.from_address(lut+0x22).value=0;bindlut(objects[driver]);C.c_uint64.from_address(data+8).value=ptr(getlut(objects[driver],8)+8)
        bindreg(objects['reg0'])
        if encode(n.mem):raise ValueError('FF data encoder failed')
        raw=out/'probe.fs';target=out/(label+'.fs.gz')
        with raw.open('rb') as src,gzip.open(target,'xb',compresslevel=6) as dst:shutil.copyfileobj(src,dst)
        if image_sha(target)!=sha(raw):raise ValueError('Compressed witness changed')
        bitmap=projection.read(target);word=0;mapping=codec.template(device.grid[49][89])
        for bit in range(16):
            values={int(bitmap[projection.index((49,89,br,bc))])-48 for br,bc in mapping[0,bit]}
            if len(values)!=1:raise ValueError('LUT INIT replicas disagree')
            word|=(1-values.pop())<<bit
        row=dict(label=label,driver=driver,lut_use_flag=flag,file=target.name,original_sha256=image_sha(target),decoded_lut_init=word,decoded_lut_init_hex=f'{word:04X}',attributes=[list(struct.unpack('<19h',C.string_at(g+6,38))) for g in groups]);records.append(row);print(label,row['decoded_lut_init_hex'],flush=True)
    save('warmup',None,0);save('standalone',None,0);save('standalone_flag',None,1);save('paired_i0','pa',1);save('paired_i3','pd',1);save('standalone_buffer',None,0,0xff00);save('standalone_buffer_flag',None,1,0xff00);save('restored',None,0)
    same=lambda a,b:payload(out/(a+'.fs.gz'))==payload(out/(b+'.fs.gz'))
    r=dict(status='ff_data_configuration_compared',records=records,standalone_equals_flagged=same('standalone','standalone_flag'),standalone_equals_paired_i0=same('standalone','paired_i0'),standalone_equals_paired_i3=same('standalone','paired_i3'),buffered_standalone_equals_paired_i3=same('standalone_buffer','paired_i3'),buffered_standalone_flag_equals_paired_i3=same('standalone_buffer_flag','paired_i3'),full_payload_restored=same('standalone','restored'),fse_sha256=device.fse.sha256,
        vendor_pnr_executed=False,production_enabled=False,hardware_verified=False,scope='Isolated native encoding; not a routed design or STA validation')
    if not r['full_payload_restored']:raise ValueError('FF data baseline payload was not restored')
    (out/'comparison.json').write_text(json.dumps(r,indent=2));compact(out)


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');a=p.parse_args();out=a.out.resolve()
    if a.worker:return worker(out)
    from gowin_probe import prepare
    from ownpr_device import DLL_SHA
    reserve(ROOT,300000000);prepare(out);(out/'probe.vg').write_text(FIXTURE);cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+'-bit\n');shutil.copyfile(__file__,out/'source.py.txt');python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic()
    with (out/'worker.log').open('w') as log:cp=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=ROOT,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=240)
    r=dict(status='captured' if cp.returncode==0 else 'failed',returncode=cp.returncode,wall_seconds=time.monotonic()-start,dll_sha256=DLL_SHA,vendor_pnr_executed=False,device_programmed=False,source_sha256=sha(out/'probe.vg'))
    if cp.returncode==0:r['comparison_sha256']=sha(out/'comparison.json')
    (out/'manifest.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
    if cp.returncode:raise SystemExit(1)


if __name__=='__main__':main()
