import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from buying import BuyReader, filter_offers, purchase_price, reputation_rank


class BuyingTests(unittest.TestCase):
    def test_purchase_modifiers_and_floor(self):
        self.assertAlmostEqual(purchase_price(1000,2,2,.04,.1),810)
        self.assertEqual(purchase_price(1000,2,3,1,1),500)
        self.assertEqual(purchase_price(1000,3,3,1,1),1000)
        self.assertIsNone(purchase_price(None,2,0,0,0))

    def test_reputation_rank_boundaries(self):
        for points,rank in [(-9000,-3),(-3000,-2),(-1000,-1),(999,0),(1000,1),(2999,1),(3000,2),(9000,3)]:
            self.assertEqual(reputation_rank(points),rank)

    def test_search_combines_words_across_item_station_and_quality(self):
        rows=[dict(name='Mining Drone Bay',category='Equipment',quality='Purple',station='Tinhead Workshop',
                   faction='Technomancers',coordinates=[3,4],item_id=12,station_id=1,distance=5),
              dict(name='Iron',category='Goods',quality='White',station='Mining Station',
                   faction='PMC',coordinates=[1,0],item_id=2,station_id=2,distance=1)]
        self.assertEqual(filter_offers(rows,'MINING purple tinhead'),[rows[0]])
        self.assertEqual(filter_offers(rows,'technomancers drone'),[rows[0]])
        self.assertFalse(filter_offers(rows,'mining orange'))
        self.assertEqual(filter_offers(rows)[0],rows[1])

    def test_numeric_price_sort_and_missing_quotes_last(self):
        rows=[dict(name=str(n),station_id=n,price=p) for n,p in enumerate([100,20,None])]
        self.assertEqual([r['price'] for r in filter_offers(rows,sort_by='price')],[20,100,None])
        self.assertEqual([r['price'] for r in filter_offers(rows,sort_by='price',descending=True)],[100,20,None])
        for row in rows:row['quantity']=row['price']
        self.assertIsNone(filter_offers(rows,sort_by='quantity',descending=True)[0]['quantity'])

    def test_reader_lists_all_categories_and_filters_unavailable_stock(self):
        station=dict(station_id=1,station='Mining Station',faction_id=1,faction='PMC',level=4,coordinates=[3,4],position=[0,0])
        weapon=dict(name=80,burst=0,rateOfFire=1,shortCooldown=.1,space=2,damage=10,critChance=0,range=100,compType=1,type=1,damageType=1)
        class Mono:
            objects={1:dict(character=10,weaponList=20,currentSectorIndex=7),
                     10:dict(SK=11,rep=12),12:dict(value=13),2:dict(stats=30),30:dict(tradeMod=0),21:weapon,
                     41:dict(itemType=3,itemID=1,rarity=1,extraData=0,tradeMode=1,targetStock=3,itemStock=90),
                     42:dict(itemType=2,itemID=2,rarity=3,extraData=0),
                     43:dict(itemType=1,itemID=0,rarity=1,extraData=0,tradeMode=0,targetStock=0,itemStock=90),
                     44:dict(itemType=4,itemID=4,rarity=1,extraData=0),
                     45:dict(itemType=3,itemID=5,rarity=1,extraData=0,tradeMode=-1,targetStock=0,itemStock=90),
                     46:dict(itemType=3,itemID=6,rarity=1,extraData=0,tradeMode=1,targetStock=0,itemStock=90),
                     47:dict(itemType=3,itemID=7,rarity=1,extraData=0,tradeMode=0,targetStock=0,itemStock=90)}
            def player(self,root):return 3,2
            def get(self,obj,name):return self.objects[obj][name]
            def selected(self,obj,names):return {n:self.objects[obj][n] for n in names if n in self.objects[obj]}
            def values(self,obj):return self.objects[obj]
            def array(self,obj,fmt):return [0]*49
            def list(self,obj,*args):return [21] if obj==20 else []
            def string(self,obj):return 'Laser'
        class Catalog:
            data=dict(purchase_skill_mod=.02,equipment={'2':dict(name='Mining Drone Bay',price=1000)},
                      ships={'4':dict(name='Leviathan',price=500000)},
                      goods={str(i):dict(name=f'Good {i}',base_price=100,trade_quantity=100,variation=.2) for i in (1,5,6,7)})
        class Connection:
            mono=Mono();root=99;catalog=Catalog()
        records=[dict(itemType=3,itemID=i,rarity=1,stock=q) for i,q in [(1,10),(5,5),(6,0),(7,2)]]
        records.append(dict(itemType=1,itemID=0,rarity=1,stock=4))
        def read_markets(reader,include_routes,market_visitor):
            market_visitor(station,[41,41,42,43,44,45,46,47],records,{})
            return dict(current=dict(coordinates=[0,0],position=[0,0]))
        reader=BuyReader(Connection())
        with patch.object(BuyReader,'game_data',return_value=1),patch('trading.TradeReader.read',read_markets):
            data=reader.read()
        rows=data['purchase_offers']
        self.assertEqual(len(rows),5)
        self.assertEqual({r['category'] for r in rows},{'Goods','Equipment','Weapon','Ship'})
        self.assertTrue(all(r['distance']==5 for r in rows))
        by_name={r['name']:r for r in rows}
        self.assertEqual(by_name['Good 1']['quantity'],7)
        self.assertEqual(by_name['Mining Drone Bay']['quality'],'Blue')
        self.assertEqual(by_name['Mining Drone Bay']['price'],5000)
        self.assertIsNone(by_name['Leviathan']['quantity'])
        self.assertEqual(by_name['Laser']['quantity'],4)
        self.assertIsNone(by_name['Good 7']['price'])


if __name__=='__main__':unittest.main()
