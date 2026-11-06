"""Isolated native device/timing data queries, never vendor P&R optimization.

Run in a disposable worker process: the SDK owns global state and is not a
reentrant service. Device/hash/part restrictions are intentional.
"""
import hashlib,math,os,struct,sys
from contextlib import contextmanager
from collections import defaultdict,deque
from functools import lru_cache
from pathlib import Path
from capture_runtime_tables import EXPECTED
from gowin_probe import BIN
import ctypes as C

_created=False


class NativeDeviceSession:
    def __init__(self,folder,full_timing=False):
        global _created
        if _created:raise RuntimeError('Use a fresh process for a native device session')
        _created=True;C.windll.kernel32.SetErrorMode(0x8003)
        self.handles=[os.add_dll_directory(str(p)) for p in (Path(sys.base_prefix)/'DLLs',Path(sys.base_prefix)/'Library/bin',BIN)]
        if hashlib.sha256((BIN/'gowin.dll').read_bytes()).hexdigest()!=EXPECTED:raise ValueError('Unreviewed native device ABI')
        self.source_sha256={
            '.dat':'19e3ec885f359ec571c09ed5d3face1b0c10971b9e0f569fdf703911993c9d33',
            '.fse':'19f859e61df5b8da361330d42d1250bd31d03bcfa2912c3ccfbf983cc4329030',
            '.tm':'a3558f35685ebc58eefce834e82059ce651b9e2a6cbca2d6668094153ca0707f'}
        for extension,digest in self.source_sha256.items():
            file=BIN.parent/'share/device/GW5AST-138C'/('GW5AST-138C'+extension)
            if hashlib.sha256(file.read_bytes()).hexdigest()!=digest:raise ValueError('Unreviewed native device data')
        folder=Path(folder).resolve();os.chdir(folder);self.dll=C.CDLL(str(BIN/'gowin.dll'));vp=C.c_void_p
        args=[str(BIN/'gowin.exe'),'-do',str(folder/'cmd.do')];av=(C.c_char_p*len(args))(*(s.encode() for s in args))
        if self.fn('?readInUserCommand@@YAHHPEAPEAD_N@Z',C.c_int,[C.c_int,C.POINTER(C.c_char_p),C.c_bool])(len(args),av,True):
            raise ValueError('Native command parsing failed')
        self.mem=self.fn('?getGlobalMemPtr@@YAPEAVglobalMem@@XZ',vp,[])()
        for name in ('?readDevice@NETLIST@@YAHPEAVglobalMem@@@Z','?readConfigAttFile@@YAHPEAVglobalMem@@@Z','?readInput@VLGPARSE@@YAHPEAVglobalMem@@@Z'):
            if self.fn(name,C.c_int,[vp])(self.mem):raise ValueError('Native device initialization failed')
        self.switch_storage=C.create_string_buffer(0x78);switch=C.addressof(self.switch_storage)
        self.fn('??0switchTable@TEMPLATE@@QEAA@XZ',vp,[vp])(switch)
        self.fn('?setSwitchTable@globalMem@@QEAAXPEAVswitchTable@TEMPLATE@@@Z',None,[vp,vp])(self.mem,switch)
        self.fn('?buildSwitchTable@switchTable@TEMPLATE@@QEAAXPEAVglobalMem@@@Z',None,[vp,vp])(switch,self.mem)
        self.chip=self.fn('?getDevChip@globalMem@@QEAAPEAVchip@NETLIST@@XZ',vp,[vp])(self.mem)
        self.fn('?buildRRGraph@chip@NETLIST@@QEAAXXZ',None,[vp])(self.chip)
        self.monitor=self.fn('?getTmMonitor@globalMem@@QEAAPEAVTmMonitor@STA@@XZ',vp,[vp])(self.mem)
        if full_timing:
            if self.fn('?readConstraints@NETLIST@@YAHPEAVglobalMem@@@Z',C.c_int,[vp])(self.mem):raise ValueError('Native constraints initialization failed')
            success=self.fn('?setTimingData@@YA_NPEAVglobalMem@@@Z',C.c_bool,[vp])(self.mem)
        else:
            # Initialize device-specific timing context before selecting the
            # speed corner, as the full setTimingData entry does. Omitting this
            # left the legacy default grade at 5 and emitted TA1112.
            C.CFUNCTYPE(None,vp)(self.dll._handle+0x1395300)(self.ptr(self.monitor+8))
            C.CFUNCTYPE(None,vp)(self.dll._handle+0x13ac550)(self.mem)
            success=self.fn('?readTimingData@TmMonitor@STA@@QEAA_NXZ',C.c_bool,[vp])(self.monitor)
        if not success:raise ValueError('Native timing initialization failed')
        self.context=self.ptr(self.monitor+8);self.library=self.ptr(self.context+0x38)
        speed=self.context+0x308;length=self.ptr(speed+16);capacity=self.ptr(speed+24)
        if length!=5 or C.string_at(self.ptr(speed) if capacity>=16 else speed,length)!=b'C1/I0':
            raise ValueError('Unexpected part-derived speed context')
        self.speed='C1/I0'
        if not self.library or struct.unpack('<3h',C.string_at(self.library+0x1c,6))!=(4,9,13):
            raise ValueError('Unreviewed native timing layout')
        self.kind=C.c_int.from_address(self.mem+0x180).value
        if self.kind!=70 or struct.unpack('<2h',C.string_at(self.chip+0x28,4))!=(111,184):
            raise ValueError('Unsupported physical device')
        self.getgrid=self.fn('?getGridByRowCol@chip@NETLIST@@QEAAPEAVgrid@2@FF@Z',vp,[vp,C.c_short,C.c_short])
        self.getnode=C.CFUNCTYPE(vp,vp,C.c_short)(self.dll._handle+0x20fcd0)
        self.getout=C.CFUNCTYPE(vp,vp,C.c_short)(self.dll._handle+0x211ed0)
        self.wire=C.CFUNCTYPE(None,vp,vp,vp,C.c_int,C.c_int)(self.dll._handle+0x14733b0)

    def fn(self,name,result,args):
        f=getattr(self.dll,name);f.restype=result;f.argtypes=args;return f

    @staticmethod
    def ptr(address):return C.c_uint64.from_address(address).value

    @lru_cache(maxsize=None)
    def node(self,coordinate):
        r,c,i=coordinate
        if not 0<=r<111 or not 0<=c<184 or i<0:raise ValueError('Invalid native node coordinate')
        grid=self.getgrid(self.chip,r,c);table=self.ptr(grid+0x10) if grid else None
        if not table or i>=C.c_short.from_address(table+8).value:raise ValueError('Native node index absent')
        node=self.getnode(table,i)
        if not node or struct.unpack('<3h',C.string_at(node,6))!=coordinate:raise ValueError('Native node coordinate mismatch')
        return node

    @lru_cache(maxsize=None)
    def fanouts(self,coordinate):
        node=self.node(coordinate);amount=C.c_short.from_address(node+6).value
        if not 0<=amount<=4096:raise ValueError('Invalid native fanout count')
        result=[]
        for index in range(amount):
            target=self.getout(node,index)
            if target:result.append(struct.unpack('<3h',C.string_at(target,6)))
        return tuple(result)

    @contextmanager
    def parent_tree(self,parents):
        """Set only explicit parent/selected flags in disposable device memory."""
        saved=[]
        try:
            for coordinate,parent in parents.items():
                node=self.node(coordinate)
                saved.append((node,self.ptr(node+0x18),C.c_ubyte.from_address(node+0x2a).value))
                C.c_uint64.from_address(node+0x18).value=self.node(parent) if parent is not None else 0
                C.c_ubyte.from_address(node+0x2a).value|=8
            yield
        finally:
            for node,parent,flags in saved:
                C.c_uint64.from_address(node+0x18).value=parent;C.c_ubyte.from_address(node+0x2a).value=flags

    def delay(self,coordinate,selector):
        return self.delay_pointer(self.node(coordinate),selector)

    def delay_pointer(self,node,selector):
        if selector not in range(3):raise ValueError('Unsupported native delay selector')
        values=(C.c_float*4)();self.wire(self.library,C.addressof(values),node,selector,self.kind)
        if not all(math.isfinite(v) for v in values):raise ValueError('Invalid native wire query')
        return tuple(values)

    def resolve_routed_nodes(self,parents):
        """Resolve special nodes only through actual native fanout pointers."""
        children=defaultdict(list);pointers={};queue=deque()
        for node,parent in parents.items():
            if parent is None:pointers[node]=self.node(node);queue.append(node)
            else:children[parent].append(node)
        while queue:
            before=queue.popleft();pointer=pointers[before];count=C.c_short.from_address(pointer+6).value
            if not 0<=count<=16000:raise ValueError('Invalid clock fanout count')
            targets=defaultdict(list)
            for index in range(count):
                target=self.getout(pointer,index)
                if target:targets[struct.unpack('<3h',C.string_at(target,6))].append(target)
            for node in children[before]:
                matches=set(targets[node])
                if len(matches)!=1:raise ValueError(f'Physical clock edge missing or ambiguous: {before} -> {node}')
                if node in pointers:raise ValueError('Cyclic or multiply driven physical tree')
                pointers[node]=matches.pop();queue.append(node)
        if set(pointers)!=set(parents):raise ValueError('Physical tree has disconnected/cyclic nodes')
        return pointers

    def initialize_import_context(self):
        """Construct native P2R ownership containers for imported carry STA.

        The inspected PlaceRoute constructor (0xea66d0..0xea683e) initializes
        pointers/containers only; it never invokes packing, placement or route.
        Empty ownership maps are not evidence of valid carry timing.
        """
        vp=C.c_void_p
        if self.ptr(self.mem+0x18):raise ValueError('Unexpected existing native P&R context')
        self.import_context_storage=C.create_string_buffer(0x200)
        address=C.addressof(self.import_context_storage)
        guard=b'\xa5'*0x40;C.memmove(address+0x1c0,guard,len(guard))
        ctor=self.fn('??0PlaceRoute@PLACEROUTE@@QEAA@PEAVglobalMem@@@Z',vp,[vp,vp])
        if ctor(address,self.mem)!=address or C.string_at(address+0x1c0,0x40)!=guard:raise ValueError('Native P&R context layout changed')
        self.fn('?setPR@globalMem@@QEAAXPEAVPlaceRoute@PLACEROUTE@@@Z',None,[vp,vp])(self.mem,address)
        p2r=self.fn('?getP2R@PlaceRoute@PLACEROUTE@@QEAAPEAVP2R@2@XZ',vp,[vp])(address)
        if not p2r or self.ptr(self.mem+0x18)!=address:raise ValueError('Native import context initialization failed')
        return dict(context_initialized=True,ownership_maps_populated=False,vendor_placement_executed=False,vendor_routing_executed=False,carry_timing_verified=False)

    @contextmanager
    def resolved_parent_tree(self,parents,pointers):
        if set(parents)!=set(pointers):raise ValueError('Incomplete resolved physical tree')
        saved=[]
        try:
            for coordinate,parent in parents.items():
                node=pointers[coordinate]
                saved.append((node,self.ptr(node+0x18),C.c_ubyte.from_address(node+0x2a).value))
                C.c_uint64.from_address(node+0x18).value=pointers[parent] if parent is not None else 0
                C.c_ubyte.from_address(node+0x2a).value|=8
            yield
        finally:
            for node,parent,flags in saved:
                C.c_uint64.from_address(node+0x18).value=parent;C.c_ubyte.from_address(node+0x2a).value=flags
