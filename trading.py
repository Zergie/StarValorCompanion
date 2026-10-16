"""Read live markets and rank feasible, profitable two-station routes."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import logging
import math
import threading
import time

from reader import Cancelled, MemoryUnavailable
from telemetry import Connection, STATE_DIR

FACTIONS=['Independent','PMC','Syndicate','Red Skull','Venghi','Children of Terra','Technomancers']


def market_price(item,mode,stock,sector_price=None):
    if mode==0:
        return sector_price if sector_price is not None and sector_price>0 else None
    ratio=max(0,min(1,stock/item['trade_quantity']))
    if mode<0:
        adjustment=(1.2-ratio)*item['variation']
        if stock<20:adjustment*=1.2
    else:adjustment=-(.2+ratio*.8)*item['variation']
    return item['base_price']*(1+adjustment)


def calculate_routes(offers,current,credits,free_cargo,extra_free=None):
    """Nearest = current→buy + buy→sell, then local travel to break sector ties."""
    extra_free=extra_free or {}; grouped=defaultdict(list); routes=[]
    for offer in offers:grouped[offer['item_id']].append(offer)
    for item,markets in grouped.items():
        for buy in markets:
            if buy['mode']<0 or buy['available']<=0 or buy['price']<=0:continue
            capacity=math.floor((free_cargo+extra_free.get(item,0)+1e-6)/buy['weight']) if buy['weight']>0 else 999999
            affordable=math.floor(credits/buy['price'])
            for sell in markets:
                if sell['mode']>0 or sell['station_id']==buy['station_id'] or sell['buy_limit']<=0:continue
                gain=sell['price']-buy['price']
                if gain<=.001:continue
                quantity=min(buy['available'],sell['buy_limit'],capacity,affordable)
                if quantity<1:continue
                to_buy=math.dist(current['coordinates'],buy['coordinates'])
                leg=math.dist(buy['coordinates'],sell['coordinates'])
                local=math.dist(current['position'],buy['position']) if to_buy==0 else 0
                if leg==0:local+=math.dist(buy['position'],sell['position'])
                routes.append(dict(item_id=item,good=buy['name'],quantity=quantity,buy=buy,sell=sell,
                                   cost=quantity*buy['price'],revenue=quantity*sell['price'],profit=quantity*gain,
                                   unit_profit=gain,to_buy=to_buy,trade_leg=leg,total_distance=to_buy+leg,local_distance=local))
    return sorted(routes,key=lambda r:(r['total_distance'],r['local_distance'],-r['profit']))


def cargo_capacity(cargo,goods,equipment,ships,weapons,optimized,extras):
    used=0; goods_weight=defaultdict(float)
    for item in cargo:
        if item['stockStationID']!=-1:continue
        item_id=str(item['itemID']);kind=item['itemType'];q=item['qnt']
        if kind==3:
            if item_id not in goods:raise MemoryUnavailable(f'Unknown cargo item #{item_id}; refresh the catalog.')
            good=goods[item_id];weight=good['weight']*(.5 if optimized and good['kind']==2 else 1)
            goods_weight[int(item_id)]+=weight*q
        elif kind==2:
            if item_id not in equipment:raise MemoryUnavailable(f'Unknown equipment #{item_id}; refresh the catalog.')
            used+=equipment[item_id]['space']*q
        elif kind==1:
            if item['itemID']>=len(weapons):raise MemoryUnavailable('Weapon cargo changed during the read.')
            used+=weapons[item['itemID']]*q
        elif kind==4:
            if item_id not in ships:raise MemoryUnavailable('Unknown ship cargo size.')
            used+=ships[item_id]['cargo_weight']*q
    for item,weight in goods_weight.items():used+=max(0,weight-extras.get(item,0))
    extra_free={item:max(0,capacity-goods_weight.get(item,0)) for item,capacity in extras.items()}
    return used,extra_free


class TradeReader:
    def __init__(self,connection):self.connection=connection;self.game_root=None
    def game_data(self):
        c=self.connection;c.connect();m=c.memory;mono=c.mono
        def valid(root):
            obj=m.pointer(root);mono.expect(obj,'GameDataInfo')
            _,ship=mono.player(c.root)
            if mono.get(obj,'spaceShipData')!=mono.get(ship,'shipData'):raise MemoryUnavailable('Galaxy is changing.')
            return obj
        if self.game_root:
            try:return valid(self.game_root)
            except MemoryUnavailable:self.game_root=None
        try:
            cache=json.loads((STATE_DIR/'trading-connection.json').read_text())
            if cache['pid']==m.pid and cache['creation']==m.creation:
                obj=valid(cache['root']);self.game_root=cache['root'];return obj
        except (OSError,ValueError,KeyError,MemoryUnavailable):pass
        pc,_=mono.player(c.root)
        self.game_root=mono.discover_static(pc,'GameData','data','GameDataInfo',c.notify,c.stop)
        obj=valid(self.game_root)
        (STATE_DIR/'trading-connection.json').write_text(json.dumps(dict(pid=m.pid,creation=m.creation,root=self.game_root)),encoding='utf-8')
        return obj
    def read(self,include_routes=True,market_visitor=None):
        c=self.connection;game=self.game_data();mono=c.mono
        gd=mono.selected(game,['stationList','sectors','currentSectorIndex','character','weaponList','customNames'])
        sectors=mono.list(gd['sectors'],limit=20000);stations=mono.list(gd['stationList'],limit=20000)
        index=gd['currentSectorIndex']
        if not 0<=index<len(sectors):raise MemoryUnavailable('Waiting for the current sector.')
        sector_cache={}
        def sector(i):
            if not 0<=i<len(sectors):raise MemoryUnavailable('Galaxy is loading.')
            if i not in sector_cache:sector_cache[i]=mono.selected(sectors[i],['x','y','marketPriceControl'])
            return sector_cache[i]
        here=sector(index);current=dict(coordinates=[here['x'],here['y']],position=[0,0])
        pc,ship=mono.player(c.root);sv=mono.selected(ship,['cs','shipData','tf'])
        cs=mono.values(sv['cs']);data=mono.selected(sv['shipData'],['cargo'])
        char=mono.selected(gd['character'],['rep','SK'])
        reps=mono.array(mono.get(char['rep'],'value'),'i');skills=mono.array(char['SK'],'i')
        optimized=len(skills)>22 and skills[22]>0
        # Transform.position is a native getter; use the last synchronized local position
        # only as a tie-breaker. Sector distances remain live and authoritative.
        synced=mono.selected(game,['playerPosX','playerPosZ'])
        current['position']=[synced.get('playerPosX',0),synced.get('playerPosZ',0)]
        catalog=c.catalog.data;goods=catalog['goods']
        cargo=[mono.values(o) for o in mono.list(data['cargo'])]
        weapons=[mono.get(o,'space') for o in mono.list(gd['weaponList'])]
        extras=defaultdict(float)
        for obj in mono.list(cs.get('extraItemSpace',0)):
            extra=mono.values(obj);extras[extra['itemID']]+=extra['extraCargoSpace']
        used,extra_free=cargo_capacity(cargo,goods,catalog['equipment'],catalog['ships'],weapons,optimized,extras)
        free=max(0,cs['cargoSpace']-used);offers=[];sale_stations=[];sale_quotes=[];skipped_prices=0;eligible=0;sector_prices={};custom_names={}
        if gd.get('customNames'):
            names=mono.selected(gd['customNames'],['nameList']).get('nameList',0)
            for obj in mono.list(names):
                rec=mono.values(obj)
                # Names are optional; tolerate future custom-name record layouts.
                if rec.get('type')==0 and rec.get('name') and 'id' in rec:
                    custom_names[rec['id']]=mono.string(rec['name'])
        for obj in stations:
            if c.stop.is_set():raise Cancelled()
            if not mono.get(obj,'discovered'):continue
            st=mono.selected(obj,['id','ownerID','state','buildingState','sectorIndex','factionIndex','buildingPlanID','level','x','y','modules','itemStock','dockingControl'])
            faction=st['factionIndex']
            if st['ownerID']!=0 or st['state']!=0 or st['buildingState']!=0:continue
            if faction>0 and (faction>=len(reps) or reps[faction]<=-1000):continue
            # Loaded stations can be locally hostile even with neutral faction reputation.
            dock=st.get('dockingControl')
            if dock:
                control=mono.selected(dock,['aisc','aISC','stationControl'])
                for aisc in control.values():
                    if not aisc:continue
                    fields=mono.selected(aisc,['ss'])
                    if fields.get('ss'):
                        ff=mono.selected(fields['ss'],['ffSys']).get('ffSys')
                        if ff and mono.get(ff,'hostileToPlayer'):break
                else:aisc=None
                if aisc:continue
            markets=[]
            for mod in mono.list(st['modules']):
                if mono.class_name(mono.object_class(mod))=='SM_Market':
                    mv=mono.selected(mod,['persistentItems','market'])
                    markets+=mono.list(mv.get('persistentItems',0))+mono.list(mv.get('market',0))
            if not markets:continue
            eligible+=1;sec=sector(st['sectorIndex']);stock=defaultdict(int);stock_records=[]
            if st['itemStock']:
                for row in mono.list(mono.get(st['itemStock'],'items')):
                    item=mono.selected(row,['itemType','itemID','stock','rarity','extraData'])
                    stock_records.append(item)
                    if item['itemType']==3:stock[item['itemID']]+=item['stock']
            sid=st['sectorIndex']
            if sid not in sector_prices:
                prices={};mpc=sec.get('marketPriceControl')
                if mpc:
                    for p in mono.list(mono.get(mpc,'prices')):
                        pv=mono.selected(p,['itemID','tradePrice']);prices[pv['itemID']]=pv['tradePrice']
                sector_prices[sid]=prices
            label=custom_names.get(st['id']) or catalog['stations'].get(str(st['buildingPlanID']),f"Station #{st['id']}")
            sale_stations.append(dict(station_id=st['id'],station=label,faction=FACTIONS[faction] if 0<=faction<len(FACTIONS) else str(faction),faction_id=faction,coordinates=[sec['x'],sec['y']],position=[st['x'],st['y']],level=st['level']))
            if market_visitor:market_visitor(sale_stations[-1],markets,stock_records,sector_prices[sid])
            seen=set()
            for market in markets:
                mi=mono.selected(market,['itemType','itemID','tradeMode','targetStock','lastStockCount','rarity','extraData'])
                if mi['itemType']!=3 or mi['itemID'] in seen:continue
                seen.add(mi['itemID']);item=goods.get(str(mi['itemID']))
                if not item:continue
                mode=mi.get('tradeMode',0);quantity=stock[mi['itemID']]
                price=market_price(item,mode,quantity,sector_prices[sid].get(mi['itemID']))
                sale_quotes.append(dict(station_id=st['id'],item_id=mi['itemID'],rarity=mi.get('rarity',1),mode=mode,price=price,extra=bool(mi.get('extraData'))))
                if not item['tradable']:continue
                if price is None:skipped_prices+=1;continue
                target=mi.get('targetStock',0)
                offers.append(dict(station_id=st['id'],station=label,faction=FACTIONS[faction] if 0<=faction<len(FACTIONS) else str(faction),
                                   sector=sid,coordinates=[sec['x'],sec['y']],position=[st['x'],st['y']],level=st['level'],
                                   item_id=mi['itemID'],name=item['name'],mode=mode,price=price,stock=quantity,
                                   available=max(0,quantity-target),buy_limit=max(0,target-quantity) if target else 999999,
                                   weight=item['weight']*(.5 if optimized and item['kind']==2 else 1)))
        if mono.get(game,'currentSectorIndex')!=index or mono.player(c.root)!=(pc,ship):
            raise MemoryUnavailable('Your sector changed while reading routes. Refreshing…')
        routes=calculate_routes(offers,current,cs['credits'],free,extra_free) if include_routes else []
        return dict(read_at=datetime.now(timezone.utc).isoformat(),pid=c.memory.pid,current=current,
                    credits=cs['credits'],cargo_free=free,cargo_capacity=cs['cargoSpace'],stations=eligible,
                    offers=len(offers),unpriced_markets=skipped_prices,routes=routes[:5000],sale_stations=sale_stations,sale_quotes=sale_quotes,sector_index=index,
                    total_routes=len(routes),source='Live discovered station markets; current-price estimates before travel costs')


class TradeWorker(threading.Thread):
    def __init__(self,events,reader_type=TradeReader,result_event='trades',status_event='trade_status'):
        super().__init__(daemon=True,name='StarValorTrading')
        self.events=events;self.stop=threading.Event();self.request=threading.Event();self.paused=threading.Event()
        self.result_event=result_event;self.status_event=status_event
        self.last_notice=0;self.connection=Connection(self.notify,self.stop);self.reader=reader_type(self.connection)
    def notify(self,text,progress=0):
        now=time.monotonic()
        if now-self.last_notice>.25:
            self.events.put((self.status_event,f'{text} · {progress:.0%}'));self.last_notice=now
    def run(self):
        try:
            while not self.stop.is_set():
                if not self.request.wait(.5):continue
                self.request.clear()
                if self.paused.is_set():continue
                try:self.events.put((self.result_event,self.reader.read()))
                except Cancelled:break
                except MemoryUnavailable as exc:self.events.put((self.status_event,str(exc)))
                except Exception:
                    logging.exception('Market scan failed');self.events.put((self.status_event,'Market read unavailable; refresh after the game finishes loading.'))
                    self.connection.close();self.reader.game_root=None
        finally:self.connection.close()
    def shutdown(self):self.stop.set();self.request.set()
