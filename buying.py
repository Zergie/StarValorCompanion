"""All stocked purchase offers in discovered station markets."""
from collections import defaultdict
import math

from reader import MemoryUnavailable
from selling import RARITY_PRICE, QUALITIES, weapon_price
from trading import TradeReader, market_price

CATEGORIES={1:'Weapon',2:'Equipment',3:'Goods',4:'Ship'}


def reputation_rank(value):
    magnitude=abs(value)
    rank=3 if magnitude>=9000 else 2 if magnitude>=3000 else 1 if magnitude>=1000 else 0
    return rank if value>=0 else -rank


def purchase_price(base,item_type,rank,skill_bonus,trade_mod):
    if base is None or not math.isfinite(base) or base<0:return None
    if item_type==3:return base
    return max(base*.5,base*(1-rank*.03-skill_bonus)*(1-trade_mod))


def filter_offers(offers,search='',sort_by='distance',descending=False):
    terms=search.casefold().split()
    rows=[r for r in offers if all(term in ' '.join(str(r.get(k,'')) for k in
          ('name','category','quality','station','faction','coordinates','item_id')).casefold() for term in terms)]
    def key(row):
        value=row.get(sort_by)
        if sort_by=='quantity' and value is None:value=math.inf
        # Missing quotes sort last regardless of direction.
        if value is None:return (True,0,row['name'].casefold())
        if isinstance(value,str):value=value.casefold()
        if descending and isinstance(value,(int,float)):value=-value
        return (False,value,row['name'].casefold(),row['station_id'])
    if sort_by in ('name','station','category','quality'):
        return sorted(rows,key=key,reverse=descending)
    return sorted(rows,key=key)


class BuyReader(TradeReader):
    def read(self):
        c=self.connection;game=self.game_data();mono=c.mono
        pc,ship=mono.player(c.root);sector_index=mono.get(game,'currentSectorIndex')
        character=mono.get(game,'character');skills=mono.array(mono.get(character,'SK'),'i')
        reps=mono.array(mono.get(mono.get(character,'rep'),'value'),'i')
        bonus=(skills[17] if len(skills)>17 else 0)*c.catalog.data['purchase_skill_mod']
        trade_mod=mono.get(mono.get(ship,'stats'),'tradeMod')
        catalog=c.catalog.data
        weapons=[mono.values(o) for o in mono.list(mono.get(game,'weaponList'))]
        offers=[]

        def visit(station,markets,stock_records,prices):
            stock=defaultdict(int)
            for record in stock_records:
                key=(record['itemType'],record['itemID'],record.get('rarity',1),record.get('extraData',0))
                stock[key]+=record['stock']
            seen=set()
            for obj in markets:
                if obj in seen:continue
                seen.add(obj)
                mi=mono.selected(obj,['itemType','itemID','rarity','extraData','tradeMode','targetStock','itemStock'])
                typ=mi['itemType'];item=mi['itemID'];rarity=mi.get('rarity',1);extra=mi.get('extraData',0)
                mode=mi.get('tradeMode',0)
                if mode<0:continue
                # Plain MarketItem stock is unlimited; DynamicMarketItem reads station stock.
                dynamic='itemStock' in mi
                quantity=max(0,stock[(typ,item,rarity,extra)]-mi.get('targetStock',0)) if dynamic else None
                if quantity==0:continue
                quality=QUALITIES[rarity] if 0<=rarity<len(QUALITIES) else str(rarity)
                name=f'Item #{item}';base=None
                if typ==3:
                    good=catalog['goods'].get(str(item),{})
                    name=good.get('name',f'Good #{item}')
                    if good and not extra:
                        base=market_price(good,mode,stock[(typ,item,rarity,extra)],prices.get(item))
                elif typ==2:
                    eq=catalog['equipment'].get(str(item),{})
                    name=eq.get('name',f'Equipment #{item}')
                    if 'price' in eq and 0<=rarity<len(RARITY_PRICE):base=eq['price']*RARITY_PRICE[rarity]
                elif typ==1:
                    if 0<=item<len(weapons):
                        weapon=weapons[item];name=mono.string(weapon['name'])
                        if 0<=rarity<len(RARITY_PRICE):base=weapon_price(weapon,rarity)
                    else:name=f'Weapon #{item}'
                elif typ==4:
                    model=catalog['ships'].get(str(item),{});name=model.get('name',f'Ship #{item}');base=model.get('price')
                # Special item data may alter price/name; retain the listing without inventing a quote.
                if extra:base=None;name+=' (special data)'
                faction=station['faction_id'];rep=reps[faction] if 0<=faction<len(reps) else 0
                price=purchase_price(base,typ,reputation_rank(rep),bonus,trade_mod)
                offers.append(dict(station,item_type=typ,item_id=item,name=name,category=CATEGORIES.get(typ,f'Type {typ}'),
                                   quality=quality,quantity=quantity,price=price))

        data=super().read(include_routes=False,market_visitor=visit)
        if mono.player(c.root)!=(pc,ship) or mono.get(game,'currentSectorIndex')!=sector_index:
            raise MemoryUnavailable('Your ship or sector changed; refresh purchase offers.')
        for offer in offers:
            offer['distance']=math.dist(data['current']['coordinates'],offer['coordinates'])
        data['purchase_offers']=filter_offers(offers)
        return data
