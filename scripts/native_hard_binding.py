"""Exclusive experimental BRAM/DSP binding, independent of vendor P&R.

This completes native associations required for pin queries and encoding.
It is not permission to accept a hard macro in the production packer: routing,
mode validation, dedicated timing and whole-design signoff remain required.
"""
import ctypes as C
import struct

# Pinned DLL constructor 0x1faee0 initializes 0xf8 ushort attributes at
# +0x22. Pointer vectors begin at +0x218; they are not attribute state.
DSP_ATTRIBUTE_COUNT=248


class HardSiteBinding:
    def __init__(self,native,obj,family,row,column):
        if family not in ('bsram','multalu27x18'):raise ValueError('Uncalibrated hard family')
        if native.kind!=70 or not 1<=row<=109 or not 1<=column<=182:raise ValueError('Unsupported hard binding device/site')
        self.native=native;self.obj=obj;self.family=family;self.active=False
        vp=C.c_void_p;fn=native.fn;ptr=native.ptr
        self.grid=native.getgrid(native.chip,row,column)
        if not self.grid or not C.c_ubyte.from_address(self.grid+0x1c).value:raise ValueError('Hard resource requires a primary grid')
        self.setphy=fn('?setPhyInst@Instance@NETLIST@@QEAAXPEAVinstance@2@@Z',None,[vp,vp])
        self.setlogic=fn('?setLogicInst@instance@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp])
        self.getphy=fn('?getPhyInst@Instance@NETLIST@@QEAAPEAVinstance@2@XZ',vp,[vp])
        self.add=fn('?addInstToGrid@grid@NETLIST@@QEAAXPEAVInstance@2@@Z',None,[vp,vp])
        self.clear=fn('?clearInstInGridList@grid@NETLIST@@QEAAXXZ',None,[vp])
        if family=='bsram':
            self.parent=fn('?getBsramByLoc@chip@NETLIST@@QEAAPEAVbsram@2@FFF@Z',vp,[vp,C.c_short,C.c_short,C.c_short])(native.chip,row,column,0)
            self.phy=self.parent;logical='Bsram';physical='bsram';allowed=(200,202);self.attribute_offset,self.attribute_size=0x8c0,186
            parent_get='?getPhyBsram@Bsram@NETLIST@@QEAAPEAVbsram@2@XZ';parent_set='?setPhyBsram@Bsram@NETLIST@@QEAAXPEAVbsram@2@@Z'
        else:
            self.parent=fn('?getNewDspByLoc@chip@NETLIST@@QEAAPEAVnewDsp@2@FF@Z',vp,[vp,C.c_short,C.c_short])(native.chip,row,column)
            self.phy=ptr(self.parent+48) if self.parent else None;logical='NewDsp';physical='multalu27x18';allowed=(185,);self.attribute_offset,self.attribute_size=0x22,DSP_ATTRIBUTE_COUNT*2
            parent_get='?getNewPhyDsp@NewDsp@NETLIST@@QEAAPEAVnewDsp@2@XZ';parent_set='?setNewPhyDsp@NewDsp@NETLIST@@QEAAXPEAVnewDsp@2@@Z'
        def classname(address):
            if not address:return None
            base=native.dll._handle;vtable=ptr(address)
            if not base<=vtable<base+0x2000000:raise ValueError('Unexpected native class pointer')
            locator=ptr(vtable-8);typeinfo=base+C.c_uint32.from_address(locator+12).value
            return C.string_at(typeinfo+16).decode('ascii')
        if classname(obj)!='.?AV'+logical+'@NETLIST@@' or classname(self.phy)!='.?AV'+physical+'@NETLIST@@':raise ValueError('Hard logical/physical class mismatch')
        subtype=fn(f'?getSubType@{logical}@NETLIST@@UEAA?AW4INS_TYPE@2@XZ',C.c_int,[vp])(obj)
        if subtype not in allowed:raise ValueError('Uncalibrated hard subtype')
        if struct.unpack('<4h',C.string_at(self.phy+0x10,8))[1:3]!=(row,column):raise ValueError('Hard physical site differs')
        if ptr(self.grid+(0x70 if family=='bsram' else 0x78))!=self.parent:raise ValueError('Hard parent grid differs')
        if ptr(self.grid+0x38) or ptr(self.phy+8):raise ValueError('Hard binding requires an exclusive empty tile')
        if family=='multalu27x18' and any(ptr(ptr(self.parent+offset)+8) for offset in (32,40,48,56,64) if ptr(self.parent+offset)):
            raise ValueError('DSP aggregate contains an occupied member')
        self.getparent=fn(parent_get,vp,[vp]);self.setparent=fn(parent_set,None,[vp,vp])
        self.old_phy=self.getphy(obj);self.old_parent=self.getparent(obj);self.old_flags=C.c_ubyte.from_address(obj+0xc0).value
        if self.old_phy:
            _,r,c,_=struct.unpack('<4h',C.string_at(self.old_phy+0x10,8))
            if 1<=r<=109 and 1<=c<=182:raise ValueError('Logical hard cell already has a device site')
        self.old_typed=ptr(self.phy+0x678) if family=='bsram' else None
        if self.old_typed:raise ValueError('BRAM encoder typed cache already occupied')
        self.old_attributes=C.string_at(self.phy+self.attribute_offset,self.attribute_size)
        self.setphy(obj,self.phy);self.setlogic(self.phy,obj);self.setparent(obj,self.parent)
        if family=='bsram':C.c_uint64.from_address(self.phy+0x678).value=obj
        self.add(self.grid,obj);self.active=True
        if ptr(self.grid+0x38)!=1:raise ValueError('Exclusive tile registration failed')

    def restore(self):
        if not self.active:raise ValueError('Hard binding was already restored')
        ptr=self.native.ptr
        # Never clear a tile that someone else changed while this binding lived.
        head=ptr(self.grid+0x30)
        if ptr(self.grid+0x38)!=1 or ptr(ptr(head)+0x10)!=self.obj or ptr(self.phy+8)!=self.obj:
            raise ValueError('Exclusive hard binding ownership changed')
        if self.family=='bsram' and ptr(self.phy+0x678)!=self.obj:raise ValueError('BRAM typed cache ownership changed')
        self.clear(self.grid);self.setlogic(self.phy,None);self.setphy(self.obj,self.old_phy);self.setparent(self.obj,self.old_parent)
        if self.family=='bsram':C.c_uint64.from_address(self.phy+0x678).value=self.old_typed or 0
        C.memmove(self.phy+self.attribute_offset,self.old_attributes,self.attribute_size)
        C.c_ubyte.from_address(self.obj+0xc0).value=self.old_flags
        if self.getphy(self.obj)!=self.old_phy or self.getparent(self.obj)!=self.old_parent or ptr(self.grid+0x38):raise ValueError('Hard binding did not restore')
        if C.string_at(self.phy+self.attribute_offset,self.attribute_size)!=self.old_attributes or C.c_ubyte.from_address(self.obj+0xc0).value!=self.old_flags:
            raise ValueError('Hard attributes/flags did not restore')
        self.active=False
