"""Isolated output-I/O-register binding and control conversion, without P&R."""
import argparse,json,os,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from ownpr_place import sha
from ownpr_device import DLL_SHA


def worker(out,encode_probe=False,preset_timing=False,constant_timing=False,encoding_cell_type='DFFCE',combined_controls=False,sites=None):
    import ctypes as C
    from native_device_session import NativeDeviceSession
    native=NativeDeviceSession(out);fn=native.fn;ptr=native.ptr;vp=C.c_void_p
    native.initialize_import_context()
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(native.mem);v=(C.c_uint64*3)()
    fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(v))
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    def string(buf):
        p=C.addressof(buf);n=ptr(p+16)
        if n>4096:raise ValueError('Invalid register name')
        return C.string_at(ptr(p) if ptr(p+24)>=16 else p,n).decode()
    objects={}
    for address in range(v[0],v[1],8):
        obj=ptr(address);b=C.create_string_buffer(32);getname(obj,C.addressof(b));objects[string(b)]=obj
    vector=fn('?getIobufVecReference@Design@NETLIST@@QEAAAEAV?$vector@PEAVIobuf@NETLIST@@V?$allocator@PEAVIobuf@NETLIST@@@std@@@std@@XZ',vp,[vp])(design)
    for address in range(ptr(vector),ptr(vector+8),8):
        obj=ptr(address);b=C.create_string_buffer(32);getname(obj,C.addressof(b));objects[string(b)]=obj
    setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp]);getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])
    bindpin=fn('?setPin2Node@Reg@NETLIST@@UEAAXXZ',None,[vp]);resetpin=fn('?resetPin2Node@Reg@NETLIST@@UEAAXXZ',None,[vp])
    pinfn=fn('?getOnePin@Reg@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint]);iopin=fn('?getOnePin@Iobuf@NETLIST@@UEAAPEAVPin@2@I@Z',vp,[vp,C.c_uint])
    bindio=fn('?setPin2Node@Iobuf@NETLIST@@UEAAXXZ',None,[vp]);logic=fn('?setIobufLogic@iobuf@NETLIST@@QEAAXPEAVIobuf@2@@Z',None,[vp,vp])
    direct=fn('?isDirectConn@Pin@NETLIST@@QEAA_NPEAVglobalMem@@@Z',C.c_bool,[vp,vp])
    route_nodes=fn('?getNodebyPin@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z',C.c_bool,[vp,vp,vp])
    def queried_nodes(pin):
        v=(C.c_uint64*3)();ok=route_nodes(pin,C.addressof(v),native.mem)
        if not v[0]<=v[1]<=v[2] or (v[1]-v[0])%8 or (v[1]-v[0])//8>8:raise ValueError('Invalid I/O-register routed pin vector')
        return dict(query_return=ok,nodes=[list(struct.unpack('<3h',C.string_at(ptr(p),6))) for p in range(v[0],v[1],8)])
    getblk=fn('?get_blk@iob@NETLIST@@QEAAPEAVioblk@2@F@Z',vp,[vp,C.c_short])
    storage=C.create_string_buffer(0x188);encoder=C.CFUNCTYPE(vp,vp,vp,C.c_bool)(native.dll._handle+0x4f8d40)(C.addressof(storage),native.mem,False)
    if encoder!=C.addressof(storage):raise ValueError('Encoder constructor mismatch')
    converters=[C.CFUNCTYPE(None,vp,vp)(native.dll._handle+rva) for rva in (0x536410,0x57d350,0x5789a0,0x578d10,0x57b010)]
    sentinel=objects['sentinel'];sources={'one':ptr(pinfn(sentinel,2)+8),'zero':ptr(pinfn(sentinel,4)+8)};records=[];timing=[]
    for r,c,block_slot in sites or ((110,135,0),(110,144,0),(110,144,1),(109,183,0),(103,183,1)):
        block=getblk(native.getgrid(native.chip,r,c),block_slot);io=ptr(block+0x28);reg=ptr(io+0x818);bufphy=ptr(ptr(block+0x10));defaults=C.string_at(io+0x840,342)
        if list(struct.unpack('<4h',C.string_at(reg+0x10,8)))!=[0,r,c,1]:raise ValueError('Unexpected native output register slot')
        for index,(typ,control_index) in enumerate((('DFFRE',4),('DFFSE',3),('DFFCE',6),('DFFPE',5))):
            obj=objects[typ.lower()];output=objects['ob'+str(index)];ce=pinfn(obj,2);control=pinfn(obj,control_index);clock=pinfn(obj,1)
            originals={p:(ptr(p+8),C.c_ubyte.from_address(p+0x4f).value) for p in (ce,control,clock)}
            setphy(obj,reg);C.c_uint64.from_address(reg+8).value=obj;bindpin(obj)
            setphy(output,bufphy);C.c_uint64.from_address(bufphy+8).value=output;logic(bufphy,output);bindio(output)
            input_vector=(C.c_uint64*3)();fn('?getAllInPins@Iobuf@NETLIST@@UEAA?AV?$vector@PEAVPin@NETLIST@@V?$allocator@PEAVPin@NETLIST@@@std@@@std@@XZ',vp,[vp,vp])(output,C.addressof(input_vector))
            if input_vector[1]-input_vector[0]!=8:raise ValueError('Expected one output buffer input')
            output_pin=ptr(input_vector[0])
            native_direct=bool(direct(output_pin,native.mem))
            output_query=queried_nodes(output_pin)
            binding={name:list(struct.unpack('<3h',C.string_at(ptr(pinfn(obj,i)+0x18),6))) if ptr(pinfn(obj,i)+0x18) else None
                for name,i in [('D',0),('CLK',1),('CE',2),('SR',control_index),('Q',7)]}
            for ce_label in ('zero','one','signal','inverted'):
                for sr_label in ('zero','one','signal','inverted'):
                    for clock_label in ('signal','inverted'):
                        C.memmove(io+0x840,defaults,342)
                        for pin,label in ((ce,ce_label),(control,sr_label),(clock,clock_label)):
                            C.c_uint64.from_address(pin+8).value=originals[pin][0] if label in ('signal','inverted') else sources[label]
                            C.c_ubyte.from_address(pin+0x4f).value=(originals[pin][1]&~0x40)|(0x40 if label=='inverted' else 0)
                        for convert in converters:convert(encoder,io)
                        if constant_timing and typ in ('DFFRE','DFFCE') and ce_label=='one' and sr_label=='zero' and clock_label=='signal':
                            from native_ioreg_timing import capture_one
                            control_name='RESET' if typ=='DFFRE' else 'CLEAR'
                            timing.append(dict(capture_one(native,obj,typ.lower(),(control_name,control_index)),io_site=[r,c,block_slot],ce_mode=ce_label,control_mode=sr_label,primitive=typ))
                        if typ==('DFFPE' if preset_timing else 'DFFCE') and ce_label in (('signal','one') if preset_timing else ('signal',)) and sr_label==clock_label=='signal':
                            if preset_timing:from native_ioreg_preset_timing import capture_one
                            else:from native_ioreg_timing import capture_one
                            timing.append(dict(capture_one(native,obj,typ.lower()),io_site=[r,c,block_slot],ce_mode=ce_label))
                        records.append(dict(site=[r,c,block_slot],cell_type=typ,ce=ce_label,control=sr_label,clock=clock_label,
                            binding=binding,native_output_direct=native_direct,output_input_route_query=output_query,
                            control_route_queries={k:queried_nodes(p) for k,p in [('CE',ce),('SR',control),('CLK',clock)]},
                            attributes=list(struct.unpack('<171h',C.string_at(io+0x840,342)))))
            for pin,(net,flags) in originals.items():C.c_uint64.from_address(pin+8).value=net;C.c_ubyte.from_address(pin+0x4f).value=flags
            resetpin(obj);setphy(obj,0);C.c_uint64.from_address(reg+8).value=0
            setphy(output,0);C.c_uint64.from_address(bufphy+8).value=0;logic(bufphy,0)
        C.memmove(io+0x840,defaults,342)
    (out/'ioreg_timing_queries.json').write_text(json.dumps(dict(registers=timing),indent=2))
    (out/'ioreg_attributes.json').write_text(json.dumps(dict(records=records,conversion_rvas=[hex(x) for x in (0x536410,0x57d350,0x5789a0,0x578d10,0x57b010)],
        scope='Isolated output-register common control conversion. Mode/default bits, routed clock, timing and bitstream correctness remain unverified.'),indent=2))
    if encode_probe:
        from native_ioreg_encoding_probe import probe
        probe(native,out,objects,encoding_cell_type,combined_controls,site_override=sites[0] if sites else None)


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');p.add_argument('--encode-probe',action='store_true');p.add_argument('--preset-timing',action='store_true');p.add_argument('--constant-timing',action='store_true');p.add_argument('--encoding-cell-type',choices=('DFFCE','DFFRE'),default='DFFCE');p.add_argument('--combined-controls',action='store_true');p.add_argument('--site',action='append');a=p.parse_args();out=a.out.resolve()
    sites=[list(map(int,s.split(','))) for s in a.site] if a.site else None
    if sites:
        from ownpr_ioreg_configuration import output_binding
        for s in sites:output_binding(s)
        if a.encode_probe and len(sites)!=1:p.error('One custom site per encoded probe')
    if (a.combined_controls or a.encoding_cell_type!='DFFCE') and not a.encode_probe:p.error('Encoding options require --encode-probe')
    if a.worker:return worker(out,a.encode_probe,a.preset_timing,a.constant_timing,a.encoding_cell_type,a.combined_controls,sites)
    from workspace_budget import reserve
    reserve(ROOT,350000000 if a.encode_probe else 20000000)
    from native_ff_attribute_oracle import fixture_netlist
    prepare(out);(out/'probe.vg').write_text(fixture_netlist());shutil.copyfile(__file__,out/'extractor_source.py.txt')
    historical_fixture_sha=json.loads((ROOT/'results/native_ff_attributes_003/manifest.json').read_text())['netlist_sha256']
    if a.encode_probe:
        cmd=out/'cmd.do';cmd.write_text(cmd.read_text()+'-bit\n')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic();rc=None;timeout=False
    with (out/'worker.log').open('w') as log:
        try:rc=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out),'--encoding-cell-type',a.encoding_cell_type]+(['--encode-probe'] if a.encode_probe else [])+(['--preset-timing'] if a.preset_timing else [])+(['--constant-timing'] if a.constant_timing else [])+(['--combined-controls'] if a.combined_controls else [])+[v for s in a.site or [] for v in ('--site',s)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=240 if a.encode_probe else 90).returncode
        except subprocess.TimeoutExpired:timeout=True
    result=dict(status='captured' if rc==0 and (out/'ioreg_attributes.json').exists() else 'failed',returncode=rc,timeout=timeout,wall_seconds=time.monotonic()-start,
        dll_sha256=DLL_SHA,source_sha256=sha(out/'probe.vg'),historical_fixture_sha256=historical_fixture_sha,
        fixture_byte_identical_to_historical=sha(out/'probe.vg')==historical_fixture_sha,timing_primitive='DFFPE' if a.preset_timing else 'DFFCE',
        custom_sites=sites,constant_timing_requested=a.constant_timing,encoding_cell_type=a.encoding_cell_type,combined_controls_requested=a.combined_controls,vendor_placement_executed=False,vendor_routing_executed=False,open_chip_database_used=False,device_programmed=False)
    if result['status']=='captured':result['attributes_sha256']=sha(out/'ioreg_attributes.json')
    if (out/'ioreg_timing_queries.json').exists():result['timing_sha256']=sha(out/'ioreg_timing_queries.json')
    if a.encode_probe:
        if (out/'ioreg_encoding_probe.json').exists():result['encoding_probe_sha256']=sha(out/'ioreg_encoding_probe.json')
        else:result['status']='failed'
    (out/'manifest.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    if result['status']=='failed':raise SystemExit(1)


if __name__=='__main__':main()
