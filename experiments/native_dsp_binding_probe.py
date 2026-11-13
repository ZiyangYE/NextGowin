"""Bind an actual MULTALU27X18 at two unplaced native DSP sites, without P&R."""
import argparse,gzip,json,os,re,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from gowin_probe import prepare
from ownpr_place import sha
from ownpr_device import DLL_SHA
from workspace_budget import reserve


def worker(out):
    import ctypes as C
    from native_device_session import NativeDeviceSession
    from native_hard_resource_capture import capture
    native=NativeDeviceSession(out);fn=native.fn;ptr=native.ptr;vp=C.c_void_p;base=native.dll._handle
    def typename(obj):
        if not obj:return None
        vt=ptr(obj)
        if not base<=vt<base+0x2000000:return None
        loc=ptr(vt-8);return C.string_at(base+C.c_uint32.from_address(loc+12).value+16).decode('ascii')
    def string(p):
        length=ptr(p+16);cap=ptr(p+24)
        if length>4096:raise ValueError('Invalid native string')
        return C.string_at(ptr(p) if cap>=16 else p,length).decode('utf8')
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(native.mem);v=(C.c_uint64*3)()
    fn('?getHierAllInsts@Design@NETLIST@@QEAAXAEAV?$vector@PEAVInstance@NETLIST@@V?$allocator@PEAVInstance@NETLIST@@@std@@@std@@@Z',None,[vp,vp])(design,C.addressof(v))
    request=json.loads((out/'resource.json').read_text()) if (out/'resource.json').exists() else {'resource':'dsp'}
    resource=request['resource']
    cls='NewDsp' if resource=='dsp' else 'Bsram'
    objects=[ptr(a) for a in range(v[0],v[1],8) if typename(ptr(a))=='.?AV'+cls+'@NETLIST@@']
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    if resource!='dsp':
        source=json.loads((ROOT/'results/coopt_vexriscv_mapping_003/05_netlist_retry/final.json').read_text())['modules']['VexRiscv']['cells'];wanted='SDPX9B' if resource=='sdpx9_36' else 'DPX9B';width=36 if resource=='sdpx9_36' else 18 if resource=='dpx9_18' else 9;selected=[]
        for candidate in objects:
            buf=C.create_string_buffer(32);getname(candidate,C.addressof(buf));cell=source[string(C.addressof(buf))]
            if cell['type']==wanted and int(cell['parameters']['BIT_WIDTH_0'],2)==width:selected.append(candidate)
        objects=selected
    expected=2 if resource=='dpx9_18' else 4
    if len(objects)!=expected:raise ValueError('VexRiscv hard-resource fixture differs')
    if request.get('instance'):
        matching=[]
        for candidate in objects:
            buffer=C.create_string_buffer(32);getname(candidate,C.addressof(buffer))
            if string(C.addressof(buffer))==request['instance']:matching.append(candidate)
        if len(matching)!=1:raise ValueError('Requested hard instance is absent or ambiguous')
        objects=matching
    obj=objects[0];getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp]);getparent=fn('?getNewPhyDsp@NewDsp@NETLIST@@QEAAPEAVnewDsp@2@XZ' if resource=='dsp' else '?getPhyBsram@Bsram@NETLIST@@QEAAPEAVbsram@2@XZ',vp,[vp])
    old_phy,old_parent=getphy(obj),getparent(obj)
    initial=dict(physical_class=typename(old_phy),site=list(struct.unpack('<4h',C.string_at(old_phy+0x10,8))) if old_phy else None,parent_class=typename(old_parent))
    (out/'initial_association.json').write_text(json.dumps(initial,indent=2))
    if initial['site'] and 1<=initial['site'][1]<=109 and 1<=initial['site'][2]<=182:raise ValueError('Fixture was already placed at a device site')
    setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp]);setlogic=fn('?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp])
    setparent=fn('?setNewPhyDsp@NewDsp@NETLIST@@QEAAXPEAVnewDsp@2@@Z' if resource=='dsp' else '?setPhyBsram@Bsram@NETLIST@@QEAAXPEAVbsram@2@@Z',None,[vp,vp]);getdsp=fn('?getNewDspByLoc@chip@NETLIST@@QEAAPEAVnewDsp@2@FF@Z',vp,[vp,C.c_short,C.c_short])
    getbram=fn('?getBsramByLoc@chip@NETLIST@@QEAAPEAVbsram@2@FFF@Z',vp,[vp,C.c_short,C.c_short,C.c_short])
    getname=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp]);nodes=fn('?getNodebyPin@Pin@NETLIST@@QEAA_NAEAV?$vector@PEAVnode@NETLIST@@V?$allocator@PEAVnode@NETLIST@@@std@@@std@@PEAVglobalMem@@@Z',C.c_bool,[vp,vp,vp])
    one=(C.c_uint64*1)(obj);vector=(C.addressof(one),C.addressof(one)+8,C.addressof(one)+8);records=[]
    encoding=[]
    if request.get('convert_attributes'):
        storage=C.create_string_buffer(0x188);constructor=C.CFUNCTYPE(vp,vp,vp,C.c_bool)(base+0x4f8d40);encoder=constructor(C.addressof(storage),native.mem,False)
        if encoder!=C.addressof(storage) or C.c_int.from_address(encoder+0xe0).value!=70:raise ValueError('Unexpected encoder attribute-conversion context')
        convert=C.CFUNCTYPE(None,vp,vp)(base+(0x565700 if resource=='dsp' else 0x54b6b0))
        if request.get('inspect_encoder'):
            pathbuf=C.create_string_buffer(32)
            C.CFUNCTYPE(vp,vp,C.c_int,vp)(base+0x4f5ae0)(C.addressof(pathbuf),native.kind,native.mem)
            if not C.CFUNCTYPE(C.c_bool,vp,vp)(base+0x53a8c0)(encoder,C.addressof(pathbuf)):raise ValueError('Encoder FSE context load failed')
            C.CFUNCTYPE(None,vp)(base+0x534750)(encoder)
            if not C.CFUNCTYPE(C.c_bool,vp)(base+0x534d30)(encoder):raise ValueError('Encoder grid context initialization failed')
            def inspect_encoding(grid):
                mapped=C.CFUNCTYPE(vp,vp,vp)(base+0x519c70)(encoder,grid)
                return dict(used=C.CFUNCTYPE(C.c_bool,vp,vp)(base+0x5302a0)(encoder,grid),mapped_class=typename(mapped),
                    mapped_site=list(struct.unpack('<2h',C.string_at(mapped+8,4))) if mapped else None,mapped_same=mapped==grid,
                    mapped_membership=ptr(mapped+0x38) if mapped else None)
    if request.get('encode'):
        import numpy as np
        from audit_native_encoding_probe import payload
        from ownpr_device import Device
        from ownpr_lut_configuration import NativeFrameProjection
        projection=NativeFrameProjection(Device());encode=fn('?generateFusemap@@YAHPEAVglobalMem@@@Z',C.c_int,[vp]);fs=out/'probe.fs'
        def encode_image(label):
            if encode(native.mem):raise ValueError('Independent hard encoding failed')
            file=out/(label+'.fs.gz')
            with fs.open('rb') as src,gzip.open(file,'xb') as dst:shutil.copyfileobj(src,dst)
            return projection.read(fs),payload(fs),file
        encode_image('warmup');baseline,base_payload,basefile=encode_image('baseline')
    locations=request.get('sites') or (((19,2),(55,77)) if resource=='dsp' else ((10,2),(64,122)))
    for index,(r,c) in enumerate(locations):
        if index and request.get('build_dsp_timing'):break
        aggregate=getdsp(native.chip,r,c) if resource=='dsp' else getbram(native.chip,r,c,0);phy=ptr(aggregate+48) if resource=='dsp' else aggregate
        hdr=list(struct.unpack('<4h',C.string_at(phy+0x10,8)))
        if typename(phy)!=('.?AVmultalu27x18@NETLIST@@' if resource=='dsp' else '.?AVbsram@NETLIST@@') or hdr[1:3]!=[r,c]:raise ValueError('Independent hard member differs')
        grid=native.getgrid(native.chip,r,c)
        def attributes():
            # DSP converter 0x222fe0 writes unsigned shorts at +0x22.
            # Constructor 0x1faee0 initializes exactly 248 attribute fields.
            return list(struct.unpack('<248H',C.string_at(phy+0x22,496))) if resource=='dsp' else list(struct.unpack('<93H',C.string_at(phy+0x8c0,186)))
        before_attributes=attributes()
        old_typed_cache=ptr(phy+0x678) if resource!='dsp' else None
        adapter=None
        if request.get('binding_adapter'):
            from native_hard_binding import HardSiteBinding
            adapter=HardSiteBinding(native,obj,'multalu27x18' if resource=='dsp' else 'bsram',r,c)
            if adapter.phy!=phy:raise ValueError('Adapter chose a different physical object')
            try:HardSiteBinding(native,obj,'multalu27x18' if resource=='dsp' else 'bsram',r,c)
            except ValueError as error:
                if 'exclusive empty tile' not in str(error):raise
            else:raise ValueError('Adapter accepted overlapping exclusive hard resources')
        if request.get('bind_typed_cache') and adapter is None:
            if resource=='dsp' or old_typed_cache:raise ValueError('Typed-cache probe requires an empty BRAM site')
            # Native table-29/30 handlers explicitly read the Bsram pointer
            # at +0x678, separate from common instance association at +8.
            C.c_uint64.from_address(phy+0x678).value=obj
        if request.get('register_grid') and adapter is None:
            if ptr(grid+0x38):raise ValueError('Independent test grid is already occupied')
            fn('?addInstToGrid@grid@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp])(grid,obj)
            if ptr(grid+0x38)!=1:raise ValueError('Grid membership insertion failed')
        if adapter is None:setphy(obj,phy);setlogic(phy,obj);setparent(obj,aggregate)
        binding_context=dict(site=[r,c],grid_class=typename(grid),grid_kind=C.c_int.from_address(grid+0x18).value,
            primary=bool(C.c_ubyte.from_address(grid+0x1c).value),grid_parent_matches=ptr(grid+(0x78 if resource=='dsp' else 0x70))==aggregate,
            typed_logical_matches=C.CFUNCTYPE(vp,vp)(base+0x214830)(phy)==obj if resource=='dsp' else None,
            grid_logical_count=ptr(grid+0x38),attributes_before=before_attributes,attributes_bound=attributes())
        if adapter is not None:binding_context['overlapping_binding_rejected']=True
        if request.get('convert_attributes'):convert(encoder,native.getgrid(native.chip,r,c))
        binding_context['attributes_converted']=attributes()
        if request.get('inspect_encoder'):binding_context['encoder_context']=inspect_encoding(grid)
        folder=out/f'site_{index:03d}';folder.mkdir();result=capture(folder,fn,native.mem,vector,getname,getphy,typename,string,nodes,False)
        if request.get('timing'):
            if request.get('build_dsp_timing'):
                built=bool(C.CFUNCTYPE(C.c_bool,vp,vp)(base+0x132dda0)(native.context,obj))
                (folder/'timing_graph_build.json').write_text(json.dumps(dict(returned=built,native_pnr_executed=False,scope='Single hard cell native timing graph only'),indent=2))
                if not built:raise ValueError('Native DSP timing graph construction failed')
            from native_hard_timing import capture_one
            capture_one(native,obj,resource,folder,full=request.get('full_timing',False),all_dsp_edges=request.get('all_dsp_edges',False))
        if request.get('encode'):variant,variant_payload,variant_file=encode_image(f'variant_{index:03d}')
        binding_context['attributes_encoded']=attributes()
        (folder/'encoding_context.json').write_text(json.dumps(binding_context,indent=2))
        if adapter is not None:adapter.restore()
        else:setphy(obj,old_phy);setlogic(phy,None);setparent(obj,old_parent)
        if request.get('bind_typed_cache') and adapter is None:C.c_uint64.from_address(phy+0x678).value=old_typed_cache or 0
        if request.get('register_grid') and adapter is None:
            fn('?clearInstInGridList@grid@NETLIST@@QEAAXXZ',None,[vp])(grid)
            if ptr(grid+0x38):raise ValueError('Independent grid membership did not restore')
        if request.get('convert_attributes'):convert(encoder,native.getgrid(native.chip,r,c))
        if getphy(obj)!=old_phy or getparent(obj)!=old_parent:raise ValueError('DSP logical associations did not restore')
        if request.get('encode'):
            restored,restored_payload,restored_file=encode_image(f'restored_{index:03d}');indices=np.flatnonzero(variant!=baseline)
            encoding.append(dict(site=[r,c],baseline=basefile.name,variant=variant_file.name,restored=restored_file.name,changed_bits=len(indices),
                changes=[dict(index=int(i),before=int(baseline[i])-48,after=int(variant[i])-48) for i in indices],full_payload_restored=restored_payload==base_payload,
                full_payload_changed=variant_payload!=base_payload,payload_lines_before=len(base_payload),payload_lines_after=len(variant_payload),
                payload_shape_changed=len(base_payload)!=len(variant_payload) or any(len(a)!=len(b) for a,b in zip(base_payload,variant_payload))))
            (out/'encoding_trials.json').write_text(json.dumps(encoding,indent=2))
            if restored_payload!=base_payload:raise ValueError('Hard configuration did not restore after unbinding')
        records.append(dict(site=[r,c],capture_sha256=sha(folder/'hard_resource_bindings.json'),pins=len(result['instances'][0]['pins']),pins_with_nodes=sum(bool(p['nodes']) for p in result['instances'][0]['pins'])))
        if request.get('timing'):records[-1]['timing_sha256']=sha(folder/'hard_timing_queries.json')
    (out/'binding_trials.json').write_text(json.dumps(dict(status='independent_hard_binding_observed',resource=resource,records=records,logical_associations_restored=True,vendor_placement_executed=False,vendor_routing_executed=False,configuration_verified=False,timing_signoff=False),indent=2))


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--worker',action='store_true');p.add_argument('--resource',choices=('dsp','dpx9_18','dpx9_9','sdpx9_36'),default='dsp');p.add_argument('--encode',action='store_true');p.add_argument('--dsp-output-register',action='store_true');p.add_argument('--convert-attributes',action='store_true');p.add_argument('--register-grid',action='store_true');p.add_argument('--inspect-encoder',action='store_true');p.add_argument('--bram-variant',choices=('read_register','init_bit','write_mode'));p.add_argument('--bind-typed-cache',action='store_true');p.add_argument('--binding-adapter',action='store_true');p.add_argument('--instance');p.add_argument('--full-timing',action='store_true');p.add_argument('--build-dsp-timing',action='store_true');p.add_argument('--timing',action='store_true');p.add_argument('--init-bit-index',type=int,default=0);p.add_argument('--all-dsp-edges',action='store_true');p.add_argument('--site',type=int,nargs=2,metavar=('ROW','COLUMN'));p.add_argument('--dsp-dynamic-enable',action='store_true');a=p.parse_args();out=a.out.resolve()
    if a.all_dsp_edges and (not a.timing or a.resource!='dsp'):p.error('All DSP edges require a DSP timing probe')
    if a.dsp_dynamic_enable and not a.dsp_output_register:p.error('Dynamic enable requires the registered-DSP fixture')
    if not 0<=a.init_bit_index<18432:p.error('Initialization bit outside one 18 Kbit block')
    if a.build_dsp_timing and (not a.timing or a.resource!='dsp'):p.error('DSP graph construction requires DSP timing probe')
    if a.binding_adapter:a.convert_attributes=True;a.register_grid=True;a.bind_typed_cache=a.resource!='dsp'
    if a.inspect_encoder and not a.convert_attributes:p.error('--inspect-encoder requires --convert-attributes')
    if a.worker:return worker(out)
    reserve(ROOT,400_000_000 if a.encode else 50_000_000);prepare(out);(out/'resource.json').write_text(json.dumps(dict(resource=a.resource,encode=a.encode,convert_attributes=a.convert_attributes,register_grid=a.register_grid,inspect_encoder=a.inspect_encoder,bind_typed_cache=a.bind_typed_cache,binding_adapter=a.binding_adapter,timing=a.timing,build_dsp_timing=a.build_dsp_timing,full_timing=a.full_timing,instance=a.instance,all_dsp_edges=a.all_dsp_edges,sites=[a.site] if a.site else None)));src=ROOT/'results/coopt_vexriscv_mapping_003/05_netlist_retry/mapped.vg';shutil.copyfile(src,out/'probe.vg');shutil.copyfile(__file__,out/'source.py.txt')
    if a.bram_variant:
        if a.resource!='dpx9_18':raise ValueError('BRAM parameter fixture currently requires DPX9B width 18')
        cells=json.loads((ROOT/'results/coopt_vexriscv_mapping_003/05_netlist_retry/final.json').read_text())['modules']['VexRiscv']['cells']
        name=next(n for n,c in cells.items() if c['type']=='DPX9B' and int(c['parameters']['BIT_WIDTH_0'],2)==18)
        key,literal={'read_register':('READ_MODE0',"1'b1"),'init_bit':('INIT_RAM_00',"288'h1"),'write_mode':('WRITE_MODE0',"2'b00")}[a.bram_variant]
        if a.bram_variant=='init_bit':key=f'INIT_RAM_{a.init_bit_index//288:02X}';literal="288'h"+format(1<<(a.init_bit_index%288),'x')
        text=(out/'probe.vg').read_text();pattern=r'(defparam\s+'+re.escape('\\'+name)+r'\s+\.'+key+r'\s*=\s*)([^;]+);'
        matches=re.findall(pattern,text)
        if len(matches)!=1:raise ValueError('BRAM fixture parameter not uniquely matched')
        text,n=re.subn(pattern,lambda m:m[1]+literal+';',text)
        (out/'probe.vg').write_text(text);(out/'fixture_mutation.json').write_text(json.dumps(dict(instance=name,parameter=key,old=matches[0][1],new=literal,calibration_only=True),indent=2))
    if a.dsp_output_register:
        if a.resource!='dsp':raise ValueError('DSP register probe requires DSP resource')
        cells=json.loads((ROOT/'results/coopt_vexriscv_mapping_003/05_netlist_retry/final.json').read_text())['modules']['VexRiscv']['cells'];name=a.instance or sorted(n for n,c in cells.items() if c['type']=='MULTALU27X18')[0]
        if name not in cells or cells[name]['type']!='MULTALU27X18':raise ValueError('Requested register fixture is not a DSP')
        text=(out/'probe.vg').read_text();text,n=re.subn(r'(defparam\s+'+re.escape('\\'+name)+r'\s+\.OREG_CLK\s*=\s*)"BYPASS";',lambda m:m[1]+'"CLK0";',text)
        if n!=1:raise ValueError('DSP fixture register parameter not uniquely matched')
        power=re.search(r'\bVCC\s+[^\s(]+\s*\(\s*\.V\(\s*(.*?)\s*\)\s*\)\s*;',text)
        block=re.search(r'(MULTALU27X18\s+'+re.escape('\\'+name)+r'\s*\()(.*?)(\);)',text,re.S)
        if not power or not block:raise ValueError('DSP register fixture lacks a constant supply or instance')
        ce_signal=power[1].strip();ce_mode='constant_one'
        if a.dsp_dynamic_enable:
            input_blocks=re.findall(r'\bIBUF\s+[^\s(]+\s*\((.*?)\);',text,re.S)
            choices=[re.search(r'\.O\s*\(\s*(.*?)\s*\)',b) for b in input_blocks]
            choices=[m[1].strip() for m in choices if m]
            clock_body=re.search(r'\.CLK\s*\(([^)]*)\)',block[2])[1]
            choices=[n for n in choices if n not in clock_body]
            if not choices:raise ValueError('No non-clock dynamic input for CE fixture')
            ce_signal=choices[0];ce_mode='dynamic_input_fixture'
        ce_width=len(cells[name]['connections']['CE']);body,n=re.subn(r'\.CE\s*\([^)]*\)',lambda m:'.CE({ '+', '.join([ce_signal+' ']*ce_width)+'})',block[2])
        if n!=1:raise ValueError('DSP register fixture CE not uniquely matched')
        text=text[:block.start()]+block[1]+body+block[3]+text[block.end():]
        (out/'probe.vg').write_text(text);(out/'fixture_mutation.json').write_text(json.dumps(dict(instance=name,parameter='OREG_CLK',old='BYPASS',new='CLK0',ce_forced_one=not a.dsp_dynamic_enable,ce_mode=ce_mode,ce_signal=ce_signal,calibration_only=True),indent=2))
    if a.encode:(out/'cmd.do').write_text((out/'cmd.do').read_text()+'-bit\n')
    python=ROOT/'binary_audit/flow/runtime/bin/python.exe';start=time.monotonic()
    with (out/'worker.log').open('w') as log:
        try:code=subprocess.run([str(python),str(Path(__file__).resolve()),'--worker','--out',str(out)],cwd=python.parent,env=dict(os.environ,PYTHONHOME=sys.base_prefix),stdout=log,stderr=subprocess.STDOUT,timeout=120).returncode
        except subprocess.TimeoutExpired:code=-1
    r=dict(status='captured' if code==0 else 'failed',returncode=code,wall_seconds=time.monotonic()-start,dll_sha256=DLL_SHA,source_netlist_sha256=sha(src),fixture_sha256=sha(out/'probe.vg'),helper_sha256=sha(ROOT/'scripts/native_hard_resource_capture.py'),vendor_pnr_executed=False)
    if a.timing:r['timing_helper_sha256']=sha(ROOT/'scripts/native_hard_timing.py')
    if a.binding_adapter:r['binding_adapter_sha256']=sha(ROOT/'scripts/native_hard_binding.py')
    if code==0:r['trials_sha256']=sha(out/'binding_trials.json')
    if a.encode:
        if (out/'encoding_trials.json').exists():
            r['encoding_trials_sha256']=sha(out/'encoding_trials.json');cases=json.loads((out/'encoding_trials.json').read_text())
            r['reversible_payload_signal']=bool(cases) and all(x['full_payload_changed'] and x['full_payload_restored'] for x in cases)
        r['hard_configuration_verified']=False
        from ownpr_clock_images import compact
        r['image_storage']=compact(out)
    (out/'manifest.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
    if code:raise SystemExit(1)


if __name__=='__main__':main()
