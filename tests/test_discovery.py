import struct
import threading
import unittest

from reader import Cancelled, MemoryUnavailable, MonoReader, ProcessMemory
from test_reader import SparseMemory


class BufferMemory(ProcessMemory):
    def __init__(self, data):
        self.base=0x100000; self.data=data
        self.regions=[(self.base,len(data),4,0x20000)]; self.bases=[self.base]
    def refresh_regions(self): pass
    def alive(self): return True
    def read_some(self,address,size):
        offset=address-self.base
        return self.data[offset:offset+size] if 0<=offset<len(self.data) else b''


class ScanTests(unittest.TestCase):
    def scan(self,data,patterns,window=None):
        memory=BufferMemory(data)
        return memory.scan(patterns,lambda p:None,threading.Event(),window)
    def test_full_pointer_verification_and_overlapping_matches(self):
        pointer=b'\x12\x34\x56\x78\x90\x01\0\0'
        data=pointer[:4]+b'WRNG'+pointer+b'aaaaa'
        self.assertEqual(self.scan(data,{'ptr':pointer,'text':b'aaa'}),
                         {'ptr':[0x100008],'text':[0x100010,0x100011,0x100012]})
    def test_chunk_boundary_matches_are_found_once(self):
        chunk=4*1024*1024; pointer=b'\x12\x34\x56\x78\x90\x01\0\0'
        for offset in (chunk-7,chunk-1,chunk,chunk+1):
            with self.subTest(offset=offset):
                data=bytearray(chunk+32); data[offset:offset+8]=pointer
                self.assertEqual(self.scan(bytes(data),{'ptr':pointer}),{'ptr':[0x100000+offset]})
    def test_false_prefixes_do_not_consume_hit_limit(self):
        pointer=b'\x12\x34\x56\x78\x90\x01\0\0'
        data=(pointer[:4]+b'WRNG')*4001+pointer
        self.assertEqual(self.scan(data,{'ptr':pointer}),{'ptr':[0x100000+4001*8]})
    def test_window_and_truncated_pointer(self):
        pointer=b'\x12\x34\x56\x78\x90\x01\0\0'
        self.assertEqual(self.scan(pointer*3,{'ptr':pointer},(0x100008,0x100014)),{'ptr':[0x100008]})
    def test_hit_limit_and_cancellation(self):
        self.assertEqual(len(self.scan(b'x'*5000,{'x':b'x'})['x']),4000)
        stop=threading.Event(); stop.set()
        with self.assertRaises(Cancelled):BufferMemory(b'test').scan({'x':b'x'},lambda p:None,stop)


class DiscoveryMemory(SparseMemory):
    def __init__(self):
        super().__init__(); self.scans=[]
    def allocate(self,size=512):
        self.next_address=(self.next_address+7)&~7
        return super().allocate(size)
    def refresh_regions(self):
        self.regions=[]
        for address in sorted(self.data):
            if self.regions and self.regions[-1][0]+self.regions[-1][1]==address:
                base,size,protect,kind=self.regions[-1]
                self.regions[-1]=(base,size+1,protect,kind)
            else:self.regions.append((address,1,4,0x20000))
        self.bases=[r[0] for r in self.regions]
    def readable(self,address): return address in self.data
    def alive(self): return True
    def scan(self,patterns,progress,stop,window=None):
        self.scans.append(window)
        return ProcessMemory.scan(self,patterns,progress,stop,window)


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        m=self.memory=DiscoveryMemory(); self.reader=MonoReader(m); self.stop=threading.Event()
        data_class=m.klass('SpaceShipData',[('isPlayerCharacter',0xa4,2,0)])
        ship_class=m.klass('SpaceShip',[('shipData',0x88,0x12,0),('m_CachedPtr',0x10,0x18,0)])
        self.klass=m.klass('PlayerControl',[('inst',8,0x12,0x10),('ss',0xb8,0x12,0),('m_CachedPtr',0x10,0x18,0)])
        self.data=m.obj(data_class); m.write(self.data+0xa4,b'\x01')
        ship=m.obj(ship_class); m.putptr(ship+0x88,self.data); m.putptr(ship+0x10,123)
        self.pc=m.obj(self.klass); m.putptr(self.pc+0xb8,ship); m.putptr(self.pc+0x10,456)
        self.vt=m.pointer(self.pc); m.write(self.vt+0x30,b'\x04')
        m.write(self.klass+0x5c,struct.pack('<i',2))
        statics=m.allocate(); self.root=statics+8; m.putptr(self.root,self.pc)
        m.putptr(self.vt+0x50,statics)
        self.runtime=m.allocate(); m.putptr(self.klass+0xd0,self.runtime); m.putptr(self.runtime+8,self.vt)
    def discover(self):return self.reader.discover(lambda text,p:None,self.stop)
    def test_local_metadata_avoids_broad_pointer_scans(self):
        self.assertEqual(self.discover(),self.root)
        self.assertEqual(sum(window is None for window in self.memory.scans),1)
    def test_distant_class_uses_broad_fallback(self):
        self.memory.next_address+=256*1024*1024
        name=self.memory.string('PlayerControl'); self.memory.putptr(self.klass+0x48,name)
        self.assertEqual(self.discover(),self.root)
        self.assertGreaterEqual(sum(window is None for window in self.memory.scans),2)
    def test_missing_runtime_uses_vtable_scan(self):
        self.memory.putptr(self.klass+0xd0,0)
        self.assertEqual(self.discover(),self.root)
        self.assertGreaterEqual(sum(window is None for window in self.memory.scans),3)
    def test_bad_runtime_slot_does_not_hide_later_valid_slot(self):
        self.memory.putptr(self.runtime+8,0xDEADBEEF)
        self.memory.putptr(self.runtime+16,self.vt)
        self.assertEqual(self.discover(),self.root)
        self.assertEqual(sum(window is None for window in self.memory.scans),1)
    def test_non_player_and_destroyed_objects_are_rejected(self):
        self.memory.write(self.data+0xa4,b'\0')
        with self.assertRaises(MemoryUnavailable):self.discover()
        self.memory.write(self.data+0xa4,b'\x01'); self.memory.putptr(self.pc+0x10,0)
        with self.assertRaises(MemoryUnavailable):self.discover()
    def test_cancelled_discovery(self):
        self.stop.set()
        with self.assertRaises(Cancelled):self.discover()


if __name__=='__main__':unittest.main()
