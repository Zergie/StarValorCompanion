"""Named, serializable ship snapshots and connection lifecycle."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import threading
from struct import error as StructError

from reader import ProcessMemory, MonoReader, MemoryUnavailable, Cancelled, find_game

APP_DIR=Path(__file__).resolve().parent
STATE_DIR=APP_DIR/'.local'
QUALITIES=['Grey','White','Green','Blue','Purple','Orange']
CREW_ROLES=['Engineer','Pilot','Navigator','Supervisor','Gunner']


def energy_value(level,bonus,modifier,positive):
    if level==-3: return 0.0
    scale=bonus if level>=0 else 0.33
    return (1+level*scale*positive)*modifier


def describe_mod(record):
    c=record.get('class',''); v=record
    if c=='SB_DroneModifiers':
        parts=[]
        if v.get('dmgMod'): parts.append(f"Drone damage +{v['dmgMod']:.0%}")
        if v.get('HPBonus'): parts.append(f"Drone HP +{v['HPBonus']:g}")
        if v.get('speedBonus'): parts.append(f"Drone speed +{v['speedBonus']:g}")
        if v.get('partsRecovery'): parts.append('Drone Parts recovery')
        if v.get('autoTargeting'): parts.append('Automatic drone targeting')
        if v.get('explodeOnDestroyMod'): parts.append('Drones explode on destruction')
        return ' · '.join(parts) or 'Drone modification'
    if c=='SB_CargoSpace':
        return f"Cargo capacity +{v.get('cargoSpaceMod',0):.0%}" if v.get('cargoSpaceMod') else f"Cargo capacity +{v.get('flatCargoSpace',0):g}"
    if c=='SB_WeaponCooling': return f"Weapon cooling +{v.get('flatCooling',0):g}/s"
    if c=='SB_WeaponDmg': return f"Weapon damage +{v.get('damageBonus',0):.0%} (type {v.get('weaponType')})"
    if c=='SB_Energy': return f"Energy generation +{v.get('energyBonus',0):g}"
    return record.get('asset_name',c).partition('.')[2].replace('_',' ') or c


class Catalog:
    def __init__(self):
        self.data=json.loads((APP_DIR/'data/catalog.json').read_text(encoding='utf-8'))
    def equipment(self,item_id): return self.data['equipment'].get(str(item_id),{})
    def ship(self,item_id): return self.data['ships'].get(str(item_id),{}).get('name',f'Ship #{item_id}')
    def modification(self,item_id):
        rec=self.data['modifications'].get(str(item_id),{})
        return dict(id=item_id,tier=rec.get('shipEnhancementRank','?'),description=describe_mod(rec) if rec else f'Modification #{item_id}')


def snapshot(mono,root,catalog):
    mem=mono.memory
    mem.refresh_regions()
    pc,ship=mono.player(root)
    addresses={'PlayerControl':pc,'SpaceShip':ship}
    ship_fields=mono.fields(ship); sv=mono.values(ship)
    for key,typ in [('shipData','SpaceShipData'),('stats','ShipStats'),('cs','CargoSystem'),
                    ('crew','ShipCrew'),('energyMmt','EnergyManagement')]:
        address=sv[key]; mono.expect(address,typ); addresses[typ]=address
    data=mono.values(addresses['SpaceShipData']); stats=mono.values(addresses['ShipStats'])
    cargo=mono.values(addresses['CargoSystem']); crew=mono.values(addresses['ShipCrew'])
    energy=mono.values(addresses['EnergyManagement'])
    levels=mono.array(energy['level'],'i'); bonuses=mono.array(energy['valueBonus'],'f')
    modifiers=mono.array(energy['modifier'],'f')
    if not len(levels)==len(bonuses)==len(modifiers)==3: raise MemoryUnavailable('Power controls are loading.')
    shield_max=stats['baseShield']*energy_value(levels[1],bonuses[1],modifiers[1],energy['generalPosMod'])
    drones=mono.values(stats['droneMods']) if stats.get('droneMods') else {}
    if stats.get('droneMods'): addresses['DroneModifiers']=stats['droneMods']
    installed=[]; used=0
    for obj in mono.list(data['equipments']):
        e=mono.values(obj); rec=catalog.equipment(e['equipmentID'])
        q=e['qnt']; used+=rec.get('space',0)*q
        installed.append(dict(id=e['equipmentID'],name=rec.get('name',f"Equipment #{e['equipmentID']}"),
                              quality=QUALITIES[e['rarity']] if 0<=e['rarity']<6 else str(e['rarity']),
                              rarity=e['rarity'],quantity=q,space=rec.get('space'),tech=rec.get('tech'),
                              address=hex(obj)))
    enhancements=[catalog.modification(i) for i in mono.list(data['enhancements'],'i')]
    weapon_count=len(mono.list(data['weapons']))
    summary=dict(ship=catalog.ship(data['shipModelID']),ship_model_id=data['shipModelID'],
                 hull=sv['currHP'],hull_max=sv['baseHP'],energy=stats['currEnergy'],energy_max=stats['baseEnergy'],
                 shields=stats['currShield'],shields_max=shield_max,credits=cargo['credits'],cargo_capacity=cargo['cargoSpace'],
                 equipment_used=used,equipment_capacity=data['equipmentSpace'],weapon_capacity=data['weaponSpace'],weapon_count=weapon_count,
                 mining_speed_bonus=stats['miningSpeedBonus'],mining_loot_bonus=stats['miningLootBonus'],
                 special_metal_bonus=stats['specialMetalBonus'],geology_bonus=stats['geologyBonus'],
                 weapon_cooling=stats['weaponCooling'],reactor_heat=sv['reactorHeat'],
                 reactor_size=stats['reactorSize'],energy_generation=stats['energyGenerated'],
                 passive_energy_draw=stats['energyExpend'],acceleration=stats['acceleration'],speed_stat=stats['maxSpeed'],
                 drone_damage_bonus=drones.get('dmgMod',0),drone_hp_bonus=drones.get('HPbonus',0),
                 drone_speed_bonus=drones.get('speedBonus',0),drone_auto_targeting=drones.get('autoTargeting',False),
                 drone_parts_recovery=drones.get('partsRecovery',False))
    if any(isinstance(v,float) and not math.isfinite(v) for v in summary.values()):
        raise MemoryUnavailable('Stats are changing. Waiting for the next snapshot.')
    if not 0<data['shipModelID']<10000 or not 0<=summary['equipment_used']<100000:
        raise MemoryUnavailable('Ship data is loading.')
    memory_rows=[]
    for typ,address in addresses.items():
        for name,field in mono.fields(address).items():
            if field.code not in [2,4,5,6,7,8,9,10,11,12,13]: continue
            val=mono.value(address,field)
            memory_rows.append(dict(object=typ,field=name,address=f'0x{address+field.offset:X}',
                                    offset=f'+0x{field.offset:X}',type={2:'Boolean',8:'Int32',12:'Float32',13:'Float64'}.get(field.code,f'Mono {field.code:X}'),value=val))
    if mono.player(root)!=(pc,ship): raise MemoryUnavailable('Ship changed during the snapshot. Refreshing…')
    return dict(pid=mem.pid,read_at=datetime.now(timezone.utc).isoformat(),summary=summary,equipment=installed,
                modifications=enhancements,crew=[dict(role=CREW_ROLES[i],efficiency=x) for i,x in enumerate(mono.array(crew['efficiency'],'f'))],
                objects={k:f'0x{v:X}' for k,v in addresses.items()},root_address=f'0x{root:X}',memory=memory_rows,
                catalog_build=catalog.data.get('build'),source='ReadProcessMemory; active PlayerControl.inst')


class Connection:
    def __init__(self,notify,stop):
        self.notify=notify; self.stop=stop; self.memory=None; self.mono=None; self.root=None
        self.catalog=Catalog()
    def close(self):
        if self.memory: self.memory.close()
        self.memory=None; self.mono=None; self.root=None
    def cache(self,root):
        STATE_DIR.mkdir(exist_ok=True)
        path=STATE_DIR/'connection.json'; temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(pid=self.memory.pid,creation=self.memory.creation,root=root)),encoding='utf-8')
        temp.replace(path)
    def connect(self,fresh=False):
        pid=find_game()
        if pid is None:
            self.close(); raise MemoryUnavailable('Waiting for Star Valor. Start the game and load a save.')
        if self.memory and (self.memory.pid!=pid or not self.memory.alive()): self.close()
        if not self.memory:
            self.memory=ProcessMemory(pid); self.mono=MonoReader(self.memory)
        if self.root and not fresh: return
        if not fresh:
            try:
                c=json.loads((STATE_DIR/'connection.json').read_text())
                if c['pid']==pid and c['creation']==self.memory.creation:
                    self.mono.player(c['root']); self.root=c['root']; return
            except (OSError,ValueError,KeyError,MemoryUnavailable,StructError): pass
        self.root=self.mono.discover(self.notify,self.stop)
        self.cache(self.root)
    def read(self):
        self.connect()
        return snapshot(self.mono,self.root,self.catalog)


