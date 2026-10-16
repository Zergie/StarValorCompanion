"""Read-only Windows / Unity Mono inspection. No target-process writes or calls."""
from __future__ import annotations

import bisect
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import struct
import threading

K = C.WinDLL('kernel32', use_last_error=True)
class MemoryUnavailable(RuntimeError):
    pass
class Cancelled(RuntimeError):
    pass
class MBI(C.Structure):
    _fields_ = [('base',C.c_void_p),('allocation',C.c_void_p),('allocation_protect',W.DWORD),
                ('size',C.c_size_t),('state',W.DWORD),('protect',W.DWORD),('kind',W.DWORD)]
class ProcessEntry(C.Structure):
    _fields_ = [('size',W.DWORD),('usage',W.DWORD),('pid',W.DWORD),('heap',C.c_size_t),
                ('module',W.DWORD),('threads',W.DWORD),('parent',W.DWORD),('priority',W.LONG),
                ('flags',W.DWORD),('exe',W.WCHAR*260)]
K.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]; K.OpenProcess.restype=C.c_void_p
K.CloseHandle.argtypes=[C.c_void_p]
K.ReadProcessMemory.argtypes=[C.c_void_p,C.c_void_p,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t)]
K.ReadProcessMemory.restype=W.BOOL
K.VirtualQueryEx.argtypes=[C.c_void_p,C.c_void_p,C.POINTER(MBI),C.c_size_t]; K.VirtualQueryEx.restype=C.c_size_t
K.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD]; K.CreateToolhelp32Snapshot.restype=C.c_void_p
K.Process32FirstW.argtypes=[C.c_void_p,C.POINTER(ProcessEntry)]
K.Process32NextW.argtypes=[C.c_void_p,C.POINTER(ProcessEntry)]
K.GetExitCodeProcess.argtypes=[C.c_void_p,C.POINTER(W.DWORD)]
K.QueryFullProcessImageNameW.argtypes=[C.c_void_p,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)]
K.GetProcessTimes.argtypes=[C.c_void_p]+[C.POINTER(W.FILETIME)]*4

def find_game():
    handle=K.CreateToolhelp32Snapshot(2,0)
    if handle==C.c_void_p(-1).value: raise MemoryUnavailable('Cannot enumerate Windows processes.')
    try:
        entry=ProcessEntry(); entry.size=C.sizeof(entry)
        valid=K.Process32FirstW(handle,C.byref(entry))
        while valid:
            if entry.exe.casefold()=='star valor.exe': return entry.pid
            valid=K.Process32NextW(handle,C.byref(entry))
    finally: K.CloseHandle(handle)
    return None

@dataclass(frozen=True)
class Field:
    name: str
    offset: int
    code: int
    static: bool
    literal: bool
    owner: str

class ProcessMemory:
    def __init__(self,pid):
        if C.sizeof(C.c_void_p)!=8: raise MemoryUnavailable('Use 64-bit Python.')
        self.pid=pid
        self.handle=K.OpenProcess(0x410,False,pid)  # QUERY_INFORMATION | VM_READ only.
        if not self.handle:
            raise MemoryUnavailable(f'Cannot read Star Valor (Windows error {C.get_last_error()}). '
                                    'If the game is elevated, run the companion at the same level.')
        self.refresh_regions()
        times=[W.FILETIME() for _ in range(4)]
        if not K.GetProcessTimes(self.handle,*(C.byref(t) for t in times)):
            self.close(); raise MemoryUnavailable('Cannot identify this process lifetime.')
        self.creation=times[0].dwLowDateTime | times[0].dwHighDateTime<<32
    def close(self):
        if self.handle: K.CloseHandle(self.handle); self.handle=None
    def alive(self):
        code=W.DWORD()
        return bool(self.handle and K.GetExitCodeProcess(self.handle,C.byref(code)) and code.value==259)
    def executable(self):
        buf=C.create_unicode_buffer(32768); size=W.DWORD(len(buf))
        return buf.value if K.QueryFullProcessImageNameW(self.handle,0,buf,C.byref(size)) else ''
    def refresh_regions(self):
        self.regions=[]; address=0
        while address<0x7fffffffffff:
            info=MBI()
            if not K.VirtualQueryEx(self.handle,address,C.byref(info),C.sizeof(info)): break
            base=info.base or 0
            if info.state==0x1000 and not info.protect&0x101:
                self.regions.append((base,info.size,info.protect,info.kind))
            if base+info.size<=address: break
            address=base+info.size
        self.bases=[r[0] for r in self.regions]
    def readable(self,address):
        i=bisect.bisect_right(self.bases,address)-1
        return i>=0 and address<self.regions[i][0]+self.regions[i][1]
    def read_some(self,address,size):
        if not 0x10000<address<0x7fffffffffff or not 0<size<=8*1024*1024: return b''
        buf=C.create_string_buffer(size); n=C.c_size_t()
        K.ReadProcessMemory(self.handle,address,buf,size,C.byref(n))
        return buf.raw[:n.value]
    def read(self,address,size):
        data=self.read_some(address,size)
        if len(data)!=size: raise MemoryUnavailable('A game object is loading or has moved. Reconnecting…')
        return data
    def pointer(self,address): return struct.unpack('<Q',self.read(address,8))[0]
    def cstring(self,address):
        if not self.readable(address): return ''
        data=self.read_some(address,160).split(b'\0',1)[0]
        if not data or len(data)>=160: return ''
        try:
            s=data.decode('utf-8'); return s if all(c.isprintable() for c in s) else ''
        except UnicodeError: return ''
    def scan(self,patterns,progress,stop,window=None):
        self.refresh_regions()
        regions=[r for r in self.regions if r[3]==0x20000 and r[2]&0xEE]
        if window:
            start,end=window
            regions=[(max(b,start),min(b+s,end)-max(b,start),p,k) for b,s,p,k in regions if b<end and b+s>start]
        total=sum(r[1] for r in regions) or 1; done=0
        overlap=max(map(len,patterns.values()))-1
        hits={k:[] for k in patterns}
        for base,size,_,_ in regions:
            for off in range(0,size,4*1024*1024):
                if stop.is_set(): raise Cancelled()
                count=min(4*1024*1024,size-off)
                data=self.read_some(base+off,min(count+overlap,size-off))
                for name,needle in patterns.items():
                    start=0
                    while len(hits[name])<4000:
                        pos=data.find(needle,start)
                        if pos<0 or pos>=count: break
                        hits[name].append(base+off+pos); start=pos+1
                done+=count; progress(done/total)
        if not self.alive(): raise MemoryUnavailable('Star Valor closed during discovery.')
        return hits

class MonoReader:
    """Offsets are read from Mono metadata; no methods are invoked in the game."""
    def __init__(self,memory): self.memory=memory; self._field_cache={}
    def class_name(self,klass): return self.memory.cstring(self.memory.pointer(klass+0x48))
    def object_class(self,obj): return self.memory.pointer(self.memory.pointer(obj))
    def expect(self,obj,name):
        klass=self.object_class(obj)
        if self.class_name(klass)!=name: raise MemoryUnavailable(f'Waiting for the active {name}.')
        return klass
    def class_fields(self,klass,inherited=True):
        key=(klass,inherited)
        if key in self._field_cache: return self._field_cache[key]
        fields=[]; seen=set(); current=klass
        while current and current not in seen:
            seen.add(current); owner=self.class_name(current); table=self.memory.pointer(current+0x98)
            for i in range(512):
                raw=self.memory.read_some(table+i*32,32)
                if len(raw)!=32: break
                typ,name,parent,offset=struct.unpack_from('<QQQi',raw)
                if parent!=current: break
                name=self.memory.cstring(name)
                if not name: break
                flags=struct.unpack('<I',self.memory.read(typ+8,4))[0]
                fields.append(Field(name,offset,(flags>>16)&255,bool(flags&0x10),bool(flags&0x40),owner))
            if not inherited: break
            current=self.memory.pointer(current+0x30)
        self._field_cache[key]=fields; return fields
    def fields(self,obj):
        return {f.name:f for f in self.class_fields(self.object_class(obj))
                if not f.static and not f.literal and 0<=f.offset<65536}
    def value(self,obj,field):
        fmt={2:'<?',3:'<H',4:'<b',5:'<B',6:'<h',7:'<H',8:'<i',9:'<I',
             10:'<q',11:'<Q',12:'<f',13:'<d'}.get(field.code,'<Q')
        return struct.unpack(fmt,self.memory.read(obj+field.offset,struct.calcsize(fmt)))[0]
    def values(self,obj): return {k:self.value(obj,f) for k,f in self.fields(obj).items()}
    def selected(self,obj,names):
        fields=self.fields(obj)
        return {k:self.value(obj,fields[k]) for k in names if k in fields}
    def string(self,obj):
        if not obj:return ''
        length=struct.unpack('<i',self.memory.read(obj+16,4))[0]
        if not 0<=length<=4096:raise MemoryUnavailable('Invalid managed string.')
        return self.memory.read(obj+20,length*2).decode('utf-16-le') if length else ''
    def get(self,obj,name):
        field=self.fields(obj).get(name)
        if not field: raise MemoryUnavailable(f'Unsupported game metadata: missing {name}.')
        return self.value(obj,field)
    def array(self,obj,fmt='Q',limit=2000):
        if not obj: return []
        count=self.memory.pointer(obj+24)
        if count>limit: raise MemoryUnavailable('A collection changed while being read.')
        return list(struct.unpack('<'+fmt*count,self.memory.read(obj+32,count*struct.calcsize(fmt)))) if count else []
    def list(self,obj,fmt='Q',limit=2000):
        if not obj: return []
        values=self.values(obj); count=values.get('_size',-1)
        if not 0<=count<=limit: raise MemoryUnavailable('Invalid collection length.')
        items=self.array(values['_items'],fmt,max(8192,limit*2))
        if count>len(items): raise MemoryUnavailable('A game collection is changing.')
        return items[:count]
    def player(self,root):
        pc=self.memory.pointer(root); self.expect(pc,'PlayerControl')
        if not self.get(pc,'m_CachedPtr'): raise MemoryUnavailable('Waiting for the player to load.')
        ship=self.get(pc,'ss'); self.expect(ship,'SpaceShip')
        if not self.get(ship,'m_CachedPtr'): raise MemoryUnavailable('Waiting for the ship to load.')
        data=self.get(ship,'shipData'); self.expect(data,'SpaceShipData')
        if not self.get(data,'isPlayerCharacter'): raise MemoryUnavailable('Waiting for the player ship.')
        return pc,ship
    def discover(self,notify,stop):
        m=self.memory
        names=m.scan({'name':b'PlayerControl\0'},lambda p:notify('Finding Mono player metadata',p/3),stop)['name']
        if not names: raise MemoryUnavailable('Load a save in Star Valor, then reconnect.')
        refs=m.scan({str(p):struct.pack('<Q',p) for p in names},
                    lambda p:notify('Resolving the player class',(1+p)/3),stop)
        classes=set()
        for matches in refs.values():
            for ref in matches:
                if ref%8: continue
                klass=ref-0x48
                try:
                    if self.class_name(klass)!='PlayerControl': continue
                    fs=self.class_fields(klass,False)
                    if any(f.name=='inst' and f.static and not f.literal for f in fs) and any(f.name=='ss' for f in fs): classes.add(klass)
                except (MemoryUnavailable,struct.error): continue
        if not classes: raise MemoryUnavailable('Player metadata not ready, or this Mono build is unsupported.')
        refs=m.scan({str(k):struct.pack('<Q',k) for k in classes},
                    lambda p:notify('Locating the active player',(2+p)/3),stop)
        for key,matches in refs.items():
            klass=int(key); inst=next(f for f in self.class_fields(klass,False) if f.name=='inst')
            count=struct.unpack('<i',m.read(klass+0x5c,4))[0]
            if not 0<=count<10000: continue
            for vt in matches:
                try:
                    if vt%8 or not m.read(vt+0x30,1)[0]&4: continue
                    root=m.pointer(vt+0x40+count*8)+inst.offset
                    pc,_=self.player(root)
                    if m.pointer(pc)==vt: return root
                except (MemoryUnavailable,struct.error): continue
        raise MemoryUnavailable('No active player ship. Load your save and reconnect.')

    def discover_static(self,anchor,classname,fieldname,expected,notify,stop):
        """Find another class in the same metadata neighborhood, then its static cell."""
        m=self.memory;anchor_class=self.object_class(anchor)
        anchors=[anchor_class,m.pointer(anchor_class+0x48),m.pointer(anchor_class+0x40)]
        window=(max(0x10000,min(anchors)-64*1024*1024),max(anchors)+64*1024*1024)
        names=m.scan({'name':classname.encode()+b'\0'},lambda p:notify('Finding trading metadata',p*.5),stop,window)['name']
        if not names:raise MemoryUnavailable('Trading metadata is not available in this Mono build.')
        refs=m.scan({str(p):struct.pack('<Q',p) for p in names},lambda p:notify('Resolving live market data',.5+p*.5),stop,window)
        for matches in refs.values():
            for ref in matches:
                if ref%8:continue
                klass=ref-0x48
                try:
                    if self.class_name(klass)!=classname:continue
                    field=next((f for f in self.class_fields(klass,False) if f.name==fieldname and f.static and not f.literal),None)
                    if not field:continue
                    runtime=m.pointer(klass+0xd0)
                    slots=struct.unpack('<i',m.read(klass+0x5c,4))[0]
                    if not 0<=slots<10000:continue
                    for offset in range(8,136,8):
                        vt=m.pointer(runtime+offset)
                        if not m.readable(vt) or m.pointer(vt)!=klass:continue
                        if not m.read(vt+0x30,1)[0]&4:continue
                        root=m.pointer(vt+0x40+slots*8)+field.offset
                        self.expect(m.pointer(root),expected)
                        return root
                except (MemoryUnavailable,struct.error):continue
        raise MemoryUnavailable('Waiting for live galaxy and trading data.')
