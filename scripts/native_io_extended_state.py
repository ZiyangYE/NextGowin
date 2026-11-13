"""Observe the hash-pinned 81-short physical I/O vector without guessing logical fields."""
import hashlib
import struct
from capture_runtime_tables import EXPECTED
from gowin_probe import BIN

PHYSICAL_ATTRIBUTES=81
# The logical accessor directly indexes only its original 16-value storage;
# these additional enums have explicit dispatch in the pinned getter.
LOGICAL_ATTRIBUTES=tuple(range(16))+(50,55,56,57,58,59,71,72,77,78)


def capture(native):
    # NativeDeviceSession installs the pinned runtime DLL directories first.
    import ctypes as C
    if hashlib.sha256((BIN/'gowin.dll').read_bytes()).hexdigest()!=EXPECTED:raise ValueError('Unreviewed extended IO ABI')
    fn=native.fn;ptr=native.ptr;vp=C.c_void_p
    design=fn('?getDesign@globalMem@@QEAAPEAVDesign@NETLIST@@XZ',vp,[vp])(native.mem)
    vector=fn('?getIobufVecReference@Design@NETLIST@@QEAAAEAV?$vector@PEAVIobuf@NETLIST@@V?$allocator@PEAVIobuf@NETLIST@@@std@@@std@@XZ',vp,[vp])(design)
    namefn=fn('?getName@Instance@NETLIST@@QEAA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@XZ',vp,[vp,vp])
    getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])
    logical=fn('?getPioValueByAttr@Iobuf@NETLIST@@QEAA?AW4PIO_VALUE@2@W4PIO_ATTR@2@@Z',C.c_int,[vp,C.c_int])
    physical=fn('?getPioAttr@iobuf@NETLIST@@QEAAFW4PIO_ATTR@2@@Z',C.c_short,[vp,C.c_int])
    attrfn=fn('?getStringfromAttr@NETLIST@@YA?AV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@W4PIO_ATTR@1@@Z',vp,[vp,C.c_int])
    def string(buffer):
        address=C.addressof(buffer);length=ptr(address+16)
        if length>4096:raise ValueError('Invalid native attribute/name string')
        return C.string_at(ptr(address) if ptr(address+24)>=16 else address,length).decode()
    attributes={}
    for i in range(PHYSICAL_ATTRIBUTES):
        buf=C.create_string_buffer(32);attrfn(C.addressof(buf),i);attributes[str(i)]=string(buf)
    first,last=ptr(vector),ptr(vector+8)
    if last<first or (last-first)%8 or last-first>8*2048:raise ValueError('Invalid IO vector')
    rows=[]
    for address in range(first,last,8):
        obj=ptr(address);buf=C.create_string_buffer(32);namefn(obj,C.addressof(buf));name=string(buf);phy=getphy(obj)
        values=[physical(phy,i) for i in range(PHYSICAL_ATTRIBUTES)] if phy else None
        raw=list((C.c_short*PHYSICAL_ATTRIBUTES).from_address(phy+0x4c)) if phy else None
        if values!=raw:raise ValueError('Physical getter/raw-vector mismatch')
        rows.append(dict(instance=name,site=list(struct.unpack('<4h',C.string_at(phy+0x10,8))) if phy else None,
            physical=values,logical_supported={str(i):logical(obj,i) for i in LOGICAL_ATTRIBUTES},physical_getter_matches_raw=True))
    return dict(schema='native-extended-physical-io-observation-v1',dll_sha256=EXPECTED,io=rows,attributes=attributes,
        physical_attribute_count=PHYSICAL_ATTRIBUTES,logical_attribute_indices=list(LOGICAL_ATTRIBUTES),
        abi=dict(physical_vector_offset=0x4c,physical_getter_rva='0x23f30',physical_initializer_rva='0x2197c0',logical_getter_rva='0x2d14c0'),
        scope='Read-only full physical-vector observation plus explicitly supported logical enums; unknown field semantics, unused-package-pad coverage and fuse-bit equivalence not established.')


def compare(before,after):
    for data in (before,after):
        if data.get('schema')!='native-extended-physical-io-observation-v1' or data.get('dll_sha256')!=EXPECTED or data.get('physical_attribute_count')!=81:raise ValueError('Unverified extended IO schema')
        if data.get('logical_attribute_indices')!=list(LOGICAL_ATTRIBUTES):raise ValueError('Wrong logical attribute coverage')
    def indexed(data):
        result={}
        for row in data['io']:
            if row['site'] is None:raise ValueError('Unbound logical IO is not a validated physical pad')
            key=tuple(row['site'])
            if key in result or len(row['physical'])!=81 or not row['physical_getter_matches_raw']:raise ValueError('Duplicate or incomplete physical IO')
            if set(row['logical_supported'])!={str(i) for i in LOGICAL_ATTRIBUTES}:raise ValueError('Incomplete supported logical IO')
            result[key]=row
        return result
    a,b=indexed(before),indexed(after)
    if set(a)!=set(b) or before['attributes']!=after['attributes']:raise ValueError('IO site/attribute coverage differs')
    diffs=[]
    for site in a:
        for i,(x,y) in enumerate(zip(a[site]['physical'],b[site]['physical'])):
            if x!=y:diffs.append(dict(site=list(site),kind='physical',attribute=i,before=x,after=y))
        for i,x in a[site]['logical_supported'].items():
            if x!=b[site]['logical_supported'][i]:diffs.append(dict(site=list(site),kind='logical',attribute=int(i),before=x,after=b[site]['logical_supported'][i]))
    return dict(status='extended_io_vectors_equal' if not diffs else 'extended_io_vectors_differ',ports=len(a),physical_values_checked=81*len(a),
        supported_logical_values_checked=len(LOGICAL_ATTRIBUTES)*len(a),differences=diffs,semantics_fully_verified=False,all_package_pads_covered=False)
