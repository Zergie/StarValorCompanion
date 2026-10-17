"""Read-only cargo sale planning; unknown quotes never imply an item is disposable."""
from collections import defaultdict
import json
import math
from pathlib import Path

from reader import MemoryUnavailable
from trading import TradeReader

RARITY_PRICE=[.4,1,2,5,10,12]
QUALITIES=['Grey','White','Green','Blue','Purple','Orange']


def load_allow_discard(path):
    try:
        settings=json.loads(Path(path).read_text(encoding='utf-8'))
        return isinstance(settings,dict) and settings.get('allow_discard') is True
    except (OSError,ValueError):return False


def save_allow_discard(path,enabled):
    save_sale_setting(path,'allow_discard',enabled)


def load_include_fleet(path):
    try:
        settings=json.loads(Path(path).read_text(encoding='utf-8'))
        return not (isinstance(settings,dict) and settings.get('include_fleet') is False)
    except (OSError,ValueError):return True


def save_include_fleet(path,enabled):
    save_sale_setting(path,'include_fleet',enabled)


def save_sale_setting(path,key,enabled):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    try:settings=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):settings={}
    if not isinstance(settings,dict):settings={}
    settings[key]=bool(enabled)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(settings,indent=2),encoding='utf-8')
    temporary.replace(path)


def resale_price(base,item_type,skill_bonus,trade_mod):
    if base is None or base<0:return None
    value=base*((.25 if item_type==4 else .1)+skill_bonus)*(1+trade_mod)
    if item_type<=2:value+=60
    return min(value,base*.5)


def weapon_price(w,rarity):
    """TWeapon.price: unmodified DPS, space/range, then type and rarity factors."""
    burst=w['burst'];period=w['rateOfFire']+w['shortCooldown']*burst
    if period<=0 or w['space']<=0:return None
    dps=w['damage']*(burst+1)/period*(1+max(0,min(100,w['critChance']))*.01)
    power=(dps+dps/w['space'])*(15+w['range']*.25)
    if w['compType']==2:power*=1.2
    if w['type']==9:power*=1.2
    power*={2:.7,3:1.2,5:6}.get(w['damageType'],1)
    return power*RARITY_PRICE[rarity]


class Exclusions:
    """Persist item identities and names, including items absent from later snapshots."""
    def __init__(self,path):
        self.path=Path(path)
        try:
            data=json.loads(self.path.read_text(encoding='utf-8'))
            self.items={str(k):str(v) for k,v in data.items() if not str(k).startswith('5:')} if isinstance(data,dict) else {}
        except (OSError,ValueError):self.items={}
    def toggle(self,key,name):
        updated=dict(self.items)
        if key in updated:del updated[key]
        else:updated[key]=name
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(updated,indent=2),encoding='utf-8')
        temporary.replace(self.path)
        self.items=updated


def inventory_rows(inventory,excluded):
    rows={row['key']:dict(row,excluded=row['key'] in excluded) for row in inventory
          if row.get('item_type')!=5 and not row['key'].startswith('5:')}
    for key,name in excluded.items():
        if key not in rows and not key.startswith('5:'):
            rows[key]=dict(key=key,name=name,loaded=0,stash=0,excluded=True)
    return sorted(rows.values(),key=lambda r:(not r['excluded'],r['name'].lower()))


def sale_inventory(inventory,include_fleet=True):
    """Project a snapshot onto the selected holds without modifying the snapshot."""
    if include_fleet:return inventory
    rows=[]
    for item in inventory:
        active=item.get('active_loaded',item['loaded']-item.get('fleet_loaded',0))
        if active+item['stash']<=0:continue
        row=dict(item,loaded=active,active_loaded=active,fleet_loaded=0)
        if 'sources' in row:
            row['sources']=[s for s in row['sources'] if s.get('active') or s.get('storage')=='stash']
        rows.append(row)
    return rows


def calculate_sales(data,excluded,allow_discard=False,include_fleet=True):
    """Maximize total proceeds at a single station, with explicit zero-value discards."""
    items=[i for i in sale_inventory(data['inventory'],include_fleet) if i.get('item_type')!=5 and not i['key'].startswith('5:')
           and i['key'] not in excluded and i['loaded']+i['stash']>0]
    if not items:return []
    plans=[]
    for station in data['sale_stations']:
        lines=[];total=0;discarded=0;unknown=0;sold=0
        for item in items:
            quote=(data['quotes'] if include_fleet else data.get('active_quotes',data['quotes']))[str(station['station_id'])].get(item['key'],dict(status='unknown',price=None))
            quantity=item['loaded']+item['stash'];status=quote['status'];price=quote.get('price')
            if status=='sell':
                value=quantity*price;total+=value;sold+=quantity
            elif status=='reject':value=0;discarded+=quantity
            else:value=None;unknown+=quantity
            lines.append(dict(item,quantity=quantity,status=status,price=price,value=value))
        if discarded and not allow_discard:continue
        if not sold:continue
        distance=math.dist(data['current']['coordinates'],station['coordinates'])
        local=math.dist(data['current']['position'],station['position']) if distance==0 else 0
        plans.append(dict(station=station,proceeds=total,discarded=discarded,unknown=unknown,sold=sold,
                          distance=distance,local_distance=local,lines=lines))
    # Complete plans precede estimates with unresolved prices; then highest total sale value.
    return sorted(plans,key=lambda p:(p['unknown']>0,-p['proceeds'],p['distance'],p['local_distance']))


class FleetCargoSnapshot:
    """Capture each distinct fleet hold; only the active ship supplies shared stash."""
    MEMBER_FIELDS=['shipData','name','alive','sectorIndex','hangarDocked','dockedStationID']

    def __init__(self,mono,character,active_data,catalog):
        self.mono=mono;self.character=character
        self.fleet_list=mono.get(character,'mercenaries')
        self.members=mono.list(self.fleet_list)
        self.member_values={obj:mono.selected(obj,self.MEMBER_FIELDS) for obj in self.members}
        self.sources=[];self.holds=[];self.entries=[]
        seen_ships=set();seen_holds=set();seen_items=set()

        def add_ship(sd,active,member=None,index=0):
            if sd in seen_ships:return
            if not sd:raise MemoryUnavailable('Fleet ship data is loading; refresh cargo again.')
            seen_ships.add(sd)
            model=mono.get(sd,'shipModelID')
            model_name=catalog['ships'].get(str(model),{}).get('name',f'Ship #{model}')
            captain=mono.string(member['name']) if member and member.get('name') else ''
            source=dict(id='active' if active else f'fleet:{index}',
                        name=f'Active · {model_name}' if active else f'Fleet {index+1} · {model_name}'+(f' ({captain})' if captain else ''),
                        active=active,model_id=model)
            if member:
                source.update({k:member[k] for k in ('sectorIndex','hangarDocked','dockedStationID') if k in member})
            self.sources.append(source)
            cargo_list=mono.get(sd,'cargo');objects=mono.list(cargo_list)
            values=[mono.values(obj) for obj in objects]
            self.holds.append((sd,cargo_list,objects,values))
            if cargo_list in seen_holds:return
            seen_holds.add(cargo_list)
            for obj,ci in zip(objects,values):
                if obj in seen_items:continue
                seen_items.add(obj)
                location=ci['stockStationID']
                if location!=-1 and not (active and location==-2 and ci['itemType']==3):continue
                if ci['itemType']==5 or ci['qnt']<=0:continue
                self.entries.append((ci,source))

        add_ship(active_data,True)
        for index,obj in enumerate(self.members):
            member=self.member_values[obj]
            if member.get('alive') is False:continue
            if 'shipData' not in member:raise MemoryUnavailable('Unsupported fleet metadata: missing shipData.')
            add_ship(member['shipData'],False,member,index)

    def validate(self):
        m=self.mono
        if m.get(self.character,'mercenaries')!=self.fleet_list or m.list(self.fleet_list)!=self.members:
            raise MemoryUnavailable('Fleet membership changed during the read; refresh cargo again.')
        for obj,values in self.member_values.items():
            if m.selected(obj,self.MEMBER_FIELDS)!=values:
                raise MemoryUnavailable('Fleet ship changed during the read; refresh cargo again.')
        for sd,cargo_list,objects,values in self.holds:
            if m.get(sd,'cargo')!=cargo_list or m.list(cargo_list)!=objects or [m.values(obj) for obj in objects]!=values:
                raise MemoryUnavailable('Fleet cargo changed during the read; refresh the sale plan.')


class SaleReader(TradeReader):
    def read(self):
        data=super().read(include_routes=False)
        c=self.connection;m=c.mono;pc,ship=m.player(c.root)
        game=self.game_data();sector_index=m.get(game,'currentSectorIndex')
        if data.get('sector_index',sector_index)!=sector_index:
            raise MemoryUnavailable('Your sector changed; refresh cargo and prices.')
        gd=m.selected(game,['character','weaponList','shipLoadouts'])
        char=m.get(gd['character'],'SK');skills=m.array(char,'i')
        skill=(skills[19] if len(skills)>19 else 0)*c.catalog.data['resale_skill_mod']
        trade=m.get(m.get(ship,'stats'),'tradeMod')
        catalog=c.catalog.data;weapon_objects=m.list(gd['weaponList'])
        weapons=[m.values(o) for o in weapon_objects]
        loads={m.get(o,'id'):m.get(o,'data') for o in m.list(gd['shipLoadouts'])}
        quotes=defaultdict(dict)
        for q in data['sale_quotes']:
            quotes[q['station_id']][(q['item_id'],q['rarity'],q['extra'])]=q

        def raw_price(ci,station_id,depth=0):
            typ=ci['itemType'];item=str(ci['itemID']);rarity=ci['rarity']
            if not 0<=rarity<len(RARITY_PRICE):return None
            if typ==2:
                eq=catalog['equipment'].get(item,{})
                return eq['price']*RARITY_PRICE[rarity] if 'price' in eq else None
            if typ==1:
                index=ci['itemID']
                return weapon_price(weapons[index],rarity) if 0<=index<len(weapons) else None
            if typ==3:
                if ci.get('extraData'):return None
                q=quotes[station_id].get((ci['itemID'],rarity,False))
                return q['price'] if q else catalog['goods'].get(item,{}).get('base_price')
            if typ==4 and depth<3:
                sd=loads.get(ci.get('shipLoadoutID'))
                base=catalog['ships'].get(item,{}).get('price')
                if not sd or base is None:return None
                traits=m.list(m.get(sd,'traits'),'i')
                if 129 in traits:base=1000+base*.005
                if 130 in traits:base*=1.3
                for obj in m.list(m.get(sd,'weapons')):
                    w=m.values(obj);v=raw_price(dict(itemType=1,itemID=w['weaponIndex'],rarity=w['rarity']),station_id,depth+1)
                    if v is None:return None
                    base+=v
                for obj in m.list(m.get(sd,'equipments')):
                    eq=m.values(obj);v=raw_price(dict(itemType=2,itemID=eq['equipmentID'],rarity=eq['rarity']),station_id,depth+1)
                    if v is None:return None
                    base+=v*eq['qnt']
                for obj in m.list(m.get(sd,'cargo')):
                    cargo=m.values(obj);v=raw_price(cargo,station_id,depth+1)
                    if v is None:return None
                    if v>0:base+=v*cargo['qnt']
                return base
            return None

        inventory={};records={};active_records={}
        fleet=FleetCargoSnapshot(m,gd['character'],m.get(ship,'shipData'),catalog)
        for ci,source in fleet.entries:
            typ=ci['itemType'];item=ci['itemID'];rarity=ci['rarity']
            if typ==5:continue
            location=ci['stockStationID']
            if location!=-1 and not (location==-2 and typ==3):continue
            if ci['qnt']<=0:continue
            # Exclusions follow item type, ID and quality, across cargo/stash and reloading.
            key=f'{typ}:{item}:{rarity}'
            if key not in inventory:
                if typ==3:name=catalog['goods'].get(str(item),{}).get('name',f'Good #{item}')
                elif typ==2:name=catalog['equipment'].get(str(item),{}).get('name',f'Equipment #{item}')
                elif typ==1:name=m.string(weapons[item]['name']) if 0<=item<len(weapons) else f'Weapon #{item}'
                elif typ==4:name=catalog['ships'].get(str(item),{}).get('name',f'Ship #{item}')
                else:name=f'Crew / item type {typ} #{item}'
                if typ in (1,2):name+=f' ({QUALITIES[rarity] if 0<=rarity<6 else rarity})'
                inventory[key]=dict(key=key,name=name,loaded=0,active_loaded=0,fleet_loaded=0,stash=0,sources=[],item_type=typ,item_id=item,rarity=rarity)
                records[key]=[];active_records[key]=[]
            inventory[key]['loaded' if location==-1 else 'stash']+=ci['qnt']
            if location==-1:inventory[key]['active_loaded' if source['active'] else 'fleet_loaded']+=ci['qnt']
            storage='cargo' if location==-1 else 'stash'
            origin=next((s for s in inventory[key]['sources'] if s['id']==source['id'] and s['storage']==storage),None)
            if origin:origin['quantity']+=ci['qnt']
            else:inventory[key]['sources'].append(dict(source,storage=storage,quantity=ci['qnt']))
            records[key].append(ci)
            if source['active']:active_records[key].append(ci)
        scoped_quotes=[]
        for scoped_records in (records,active_records):
            sale_quotes={}
            for station in data['sale_stations']:
                sid=station['station_id'];out={};sale_quotes[str(sid)]=out
                for key,item in inventory.items():
                    typ=item['item_type'];cis=scoped_records[key];status='sell';value=0;quantity=0
                    if typ==3:
                        q=quotes[sid].get((item['item_id'],item['rarity'],False))
                        if any(ci.get('extraData') for ci in cis):status='unknown'
                        elif not q or q['mode']>0:status='reject'
                        elif q['price'] is None:status='unknown'
                        elif q['price']<0:status='reject'
                    elif typ not in (1,2,4):status='unknown'
                    if status=='sell':
                        for ci in cis:
                            price=raw_price(ci,sid)
                            if price is None:status='unknown';break
                            if typ!=3:price=resale_price(price,typ,skill,trade)
                            if price is None or not math.isfinite(price):status='unknown';break
                            value+=price*ci['qnt'];quantity+=ci['qnt']
                    out[key]=dict(status=status,price=value/quantity if quantity and status=='sell' else None)
            scoped_quotes.append(sale_quotes)
        if m.player(c.root)!=(pc,ship) or m.get(game,'currentSectorIndex')!=sector_index:
            raise MemoryUnavailable('Cargo changed during the read; refresh the sale plan.')
        fleet.validate()
        data.update(inventory=list(inventory.values()),quotes=scoped_quotes[0],active_quotes=scoped_quotes[1],fleet_ships=fleet.sources)
        return data
