import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from selling import Exclusions, SaleReader, calculate_sales, inventory_rows, resale_price, weapon_price


def example():
    return dict(read_at='2026-10-15T09:00:00+00:00',current=dict(coordinates=[0,0],position=[0,0]),
                inventory=[dict(key='3:1:1',name='Iron',loaded=10,stash=20,item_type=3,item_id=1,rarity=1),
                           dict(key='3:2:1',name='Scrap',loaded=5,stash=0,item_type=3,item_id=2,rarity=1)],
                sale_stations=[dict(station_id=1,station='All goods',level=1,faction='Independent',coordinates=[1,0],position=[0,0]),
                               dict(station_id=2,station='High price',level=2,faction='Independent',coordinates=[5,0],position=[0,0])],
                quotes={'1':{'3:1:1':dict(status='sell',price=100),'3:2:1':dict(status='sell',price=20)},
                        '2':{'3:1:1':dict(status='sell',price=150),'3:2:1':dict(status='reject',price=None)}})


class SellingTests(unittest.TestCase):
    def test_stash_and_cargo_combined_at_one_station(self):
        plans=calculate_sales(example(),{},False)
        self.assertEqual(len(plans),1)
        self.assertEqual(plans[0]['proceeds'],3100)
        self.assertEqual(plans[0]['sold'],35)

    def test_discard_permits_higher_value_station(self):
        plan=calculate_sales(example(),{},True)[0]
        self.assertEqual(plan['station']['station_id'],2)
        self.assertEqual(plan['proceeds'],4500)
        self.assertEqual(plan['discarded'],5)
        self.assertEqual(plan['lines'][1]['status'],'reject')

    def test_exclusions_protect_both_cargo_and_stash(self):
        plans=calculate_sales(example(),{'3:1:1':'Iron'},True)
        self.assertEqual(len(plans),1)
        self.assertEqual(plans[0]['proceeds'],100)
        self.assertTrue(all(i['key']!='3:1:1' for i in plans[0]['lines']))
        self.assertFalse(calculate_sales(example(),{'3:1:1':'Iron','3:2:1':'Scrap'},True))

    def test_absent_exclusions_survive_restart_and_reappear_unchecked(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            path=Path(folder)/'exclusions.json';saved=Exclusions(path)
            saved.toggle('3:1:1','Iron');loaded=Exclusions(path)
            row=inventory_rows([],loaded.items)[0]
            self.assertEqual((row['loaded'],row['stash'],row['excluded']),(0,0,True))
            self.assertTrue(inventory_rows(example()['inventory'],loaded.items)[0]['excluded'])
            loaded.toggle('3:1:1','Iron')
            self.assertEqual(Exclusions(path).items,{})

    def test_unknown_quote_is_review_never_discard(self):
        data=example();data['quotes']['2']['3:2:1']=dict(status='unknown',price=None)
        plans=calculate_sales(data,{},True)
        self.assertEqual(plans[0]['station']['station_id'],1)
        unknown=plans[1]
        self.assertEqual(unknown['discarded'],0)
        self.assertEqual(unknown['unknown'],5)
        self.assertIsNone(unknown['lines'][1]['value'])

    def test_equal_revenue_prefers_nearer_station(self):
        data=example();data['quotes']['2']=copy.deepcopy(data['quotes']['1'])
        self.assertEqual(calculate_sales(data,{},True)[0]['station']['station_id'],1)

    def test_no_station_with_only_discards(self):
        data=example();data['quotes']['1']=copy.deepcopy(data['quotes']['2'])
        for quotes in data['quotes'].values():quotes['3:1:1']['status']='reject'
        self.assertFalse(calculate_sales(data,{},True))

    def test_equipment_resale_skill_trade_and_cap(self):
        self.assertEqual(resale_price(1000,2,0,0),160)
        self.assertEqual(resale_price(1000,2,.1,.2),300)
        self.assertEqual(resale_price(1000,2,1,1),500)
        self.assertEqual(resale_price(1000,4,0,0),250)

    def test_weapon_formula_matches_raw_dps(self):
        w=dict(burst=0,rateOfFire=1,shortCooldown=.1,space=2,damage=10,critChance=0,range=100,compType=1,type=1,damageType=1)
        self.assertEqual(weapon_price(w,1),600)
        self.assertEqual(weapon_price(w,3),3000)

    def test_reader_loads_only_ship_cargo_and_resource_stash(self):
        class Mono:
            def __init__(self):
                self.objects={1:{'character':10,'weaponList':0,'shipLoadouts':0,'currentSectorIndex':7},
                              10:{'SK':11},2:{'stats':20,'shipData':30},20:{'tradeMod':0},30:{'cargo':40},
                              41:dict(itemType=3,itemID=1,rarity=1,qnt=10,stockStationID=-1,extraData=0),
                              42:dict(itemType=3,itemID=1,rarity=1,qnt=20,stockStationID=-2,extraData=0),
                              43:dict(itemType=3,itemID=1,rarity=1,qnt=999,stockStationID=123,extraData=0),
                              44:dict(itemType=5,itemID=123,rarity=1,qnt=1,stockStationID=-1,extraData=0)}
            def player(self,root):return 3,2
            def get(self,obj,name):return self.objects[obj][name]
            def selected(self,obj,names):return {n:self.objects[obj][n] for n in names}
            def values(self,obj):return self.objects[obj]
            def array(self,obj,fmt):return [0]*49
            def list(self,obj,*args):return [41,42,43,44] if obj==40 else []
        class Catalog:
            data=dict(resale_skill_mod=.01,goods={'1':dict(name='Iron',base_price=90)})
        class Connection:
            mono=Mono();root=99;catalog=Catalog()
        data=example();data['sale_quotes']=[dict(station_id=1,item_id=1,rarity=1,extra=False,mode=-1,price=100)]
        reader=SaleReader(Connection())
        with patch.object(SaleReader,'game_data',return_value=1),patch('trading.TradeReader.read',return_value=data):
            result=reader.read()
        self.assertEqual(len(result['inventory']),1)
        self.assertEqual((result['inventory'][0]['loaded'],result['inventory'][0]['stash']),(10,20))
        self.assertEqual(result['quotes']['1']['3:1:1'],dict(status='sell',price=100))
        self.assertEqual(result['quotes']['2']['3:1:1']['status'],'reject')


if __name__=='__main__':unittest.main()
