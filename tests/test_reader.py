import struct
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reader import MonoReader, MemoryUnavailable
from telemetry import energy_value, describe_mod


class SparseMemory:
    """Synthetic Mono heap: exercise metadata parsing without a real process."""
    def __init__(self): self.data={}; self.next_address=0x100000
    def allocate(self,size=512):
        addr=self.next_address; self.next_address+=size+256
        self.write(addr,bytes(size)); return addr
    def write(self,addr,data): self.data.update({addr+i:v for i,v in enumerate(data)})
    def pointer(self,addr): return struct.unpack('<Q',self.read(addr,8))[0]
    def putptr(self,addr,val): self.write(addr,struct.pack('<Q',val))
    def read_some(self,addr,size):
        out=bytearray()
        for i in range(size):
            if addr+i not in self.data:break
            out.append(self.data[addr+i])
        return bytes(out)
    def read(self,addr,size):
        data=self.read_some(addr,size)
        if len(data)!=size:raise MemoryUnavailable('unmapped')
        return data
    def string(self,s):
        a=self.allocate(len(s)+1);self.write(a,s.encode()+b'\0');return a
    def cstring(self,addr):return self.read_some(addr,160).split(b'\0',1)[0].decode()
    def klass(self,name,fields,parent=0):
        klass=self.allocate();self.putptr(klass+0x48,self.string(name));self.putptr(klass+0x30,parent)
        table=self.allocate((len(fields)+1)*32);self.putptr(klass+0x98,table)
        for i,(name,offset,code,attrs) in enumerate(fields):
            typ=self.allocate(16);self.write(typ+8,struct.pack('<I',code<<16|attrs))
            self.write(table+i*32,struct.pack('<QQQiI',typ,self.string(name),klass,offset,0))
        return klass
    def obj(self,klass):
        vt=self.allocate();self.putptr(vt,klass);obj=self.allocate();self.putptr(obj,vt);return obj


class MetadataTests(unittest.TestCase):
    def setUp(self): self.mem=SparseMemory();self.mono=MonoReader(self.mem)
    def test_reordered_and_inherited_fields(self):
        base=self.mem.klass('Entity',[('currHP',0x5c,12,0)])
        ship=self.mem.klass('SpaceShipData',[('weaponSpace',0x74,12,0),('equipments',0x20,0x15,0),('inst',0,0x12,0x10)],base)
        obj=self.mem.obj(ship);self.mem.write(obj+0x74,struct.pack('<f',4));self.mem.write(obj+0x5c,struct.pack('<f',358.25));self.mem.putptr(obj+0x20,0xABC000)
        values=self.mono.values(obj)
        self.assertEqual(values,{'weaponSpace':4.0,'equipments':0xABC000,'currHP':358.25})
    def test_array_and_list_honor_count_not_capacity(self):
        klass=self.mem.klass('List`1',[('_items',0x10,0x1d,0),('_size',0x18,8,0)])
        obj=self.mem.obj(klass);arr=self.mem.allocate();self.mem.putptr(arr+24,4)
        self.mem.write(arr+32,struct.pack('<4i',119,78,94,126));self.mem.putptr(obj+0x10,arr);self.mem.write(obj+0x18,struct.pack('<i',2))
        self.assertEqual(self.mono.list(obj,'i'),[119,78])
        self.mem.write(obj+0x18,struct.pack('<i',5))
        with self.assertRaises(MemoryUnavailable): self.mono.list(obj,'i')
    def test_oversized_array_is_rejected_before_allocation(self):
        arr=self.mem.allocate();self.mem.putptr(arr+24,0xFFFFFFFF)
        with self.assertRaises(MemoryUnavailable):self.mono.array(arr)
    def test_player_follows_changed_singleton_and_rejects_destroyed_unity_object(self):
        data_class=self.mem.klass('SpaceShipData',[('isPlayerCharacter',0xa4,2,0)])
        ship_class=self.mem.klass('SpaceShip',[('shipData',0x88,0x12,0),('m_CachedPtr',0x10,0x18,0)])
        pc_class=self.mem.klass('PlayerControl',[('ss',0xb8,0x12,0),('m_CachedPtr',0x10,0x18,0)])
        data=self.mem.obj(data_class);self.mem.write(data+0xa4,b'\x01')
        ship=self.mem.obj(ship_class);self.mem.putptr(ship+0x88,data);self.mem.putptr(ship+0x10,123)
        root=self.mem.allocate();pcs=[self.mem.obj(pc_class) for _ in range(2)]
        for pc in pcs:self.mem.putptr(pc+0xb8,ship);self.mem.putptr(pc+0x10,456)
        self.mem.putptr(root,pcs[0]);self.assertEqual(self.mono.player(root),(pcs[0],ship))
        self.mem.putptr(root,pcs[1]);self.mem.putptr(pcs[0]+0x10,0)
        self.assertEqual(self.mono.player(root),(pcs[1],ship))
        self.mem.putptr(ship+0x10,0)
        with self.assertRaises(MemoryUnavailable):self.mono.player(root)
    def test_non_player_ship_is_rejected(self):
        with self.assertRaises(MemoryUnavailable):self.mono.expect(self.mem.obj(self.mem.klass('Drone',[])),'SpaceShip')


class InterpretationTests(unittest.TestCase):
    def test_shield_power_formula_includes_crew_modifier(self):
        self.assertAlmostEqual(300*energy_value(1,.4,1.08402729,1),455.2914618)
        self.assertEqual(energy_value(-3,.4,1.2,1),0)
        self.assertAlmostEqual(energy_value(-1,.4,1,1),.67)
    def test_modification_retains_both_targeting_and_damage(self):
        text=describe_mod({'class':'SB_DroneModifiers','dmgMod':.15,'autoTargeting':True})
        self.assertIn('+15%',text);self.assertIn('Automatic drone targeting',text)
    def test_recovery_is_not_described_as_targeting(self):
        text=describe_mod({'class':'SB_DroneModifiers','partsRecovery':True})
        self.assertIn('Parts recovery',text);self.assertNotIn('targeting',text)


if __name__=='__main__':unittest.main()
