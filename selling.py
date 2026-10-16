"""Read-only cargo sale planning; unknown quotes never imply an item is disposable."""
from collections import defaultdict
import json
import math
from pathlib import Path

from reader import MemoryUnavailable
from trading import TradeReader

RARITY_PRICE=[.4,1,2,5,10,12]
QUALITIES=['Grey','White','Green','Blue','Purple','Orange']


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


def calculate_sales(data,excluded,allow_discard=False):
    """Maximize total proceeds at a single station, with explicit zero-value discards."""
    items=[i for i in data['inventory'] if i.get('item_type')!=5 and not i['key'].startswith('5:')
           and i['key'] not in excluded and i['loaded']+i['stash']>0]
    if not items:return []
    plans=[]
    for station in data['sale_stations']:
        lines=[];total=0;discarded=0;unknown=0;sold=0
        for item in items:
            quote=data['quotes'][str(station['station_id'])].get(item['key'],dict(status='unknown',price=None))
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

        inventory={};records={}
        sd=m.get(ship,'shipData')
        cargo_objects=m.list(m.get(sd,'cargo'))
        cargo_snapshot=[m.values(obj) for obj in cargo_objects]
        for ci in cargo_snapshot:
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
                inventory[key]=dict(key=key,name=name,loaded=0,stash=0,item_type=typ,item_id=item,rarity=rarity)
                records[key]=[]
            inventory[key]['loaded' if location==-1 else 'stash']+=ci['qnt']
            records[key].append(ci)
        sale_quotes={}
        for station in data['sale_stations']:
            sid=station['station_id'];out={};sale_quotes[str(sid)]=out
            for key,item in inventory.items():
                typ=item['item_type'];cis=records[key];status='sell';value=0;quantity=0
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
        if m.player(c.root)!=(pc,ship) or m.get(game,'currentSectorIndex')!=sector_index:
            raise MemoryUnavailable('Cargo changed during the read; refresh the sale plan.')
        if m.list(m.get(sd,'cargo'))!=cargo_objects or [m.values(obj) for obj in cargo_objects]!=cargo_snapshot:
            raise MemoryUnavailable('Inventory changed during the read; refresh the sale plan.')
        data.update(inventory=list(inventory.values()),quotes=sale_quotes)
        return data
