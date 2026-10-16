import copy
import unittest
from unittest.mock import patch

from reader import MemoryUnavailable
from selling import FleetCargoSnapshot, SaleReader, calculate_sales
from test_selling import example


class FleetMono:
    def __init__(self):
        self.objects={1:dict(character=10,weaponList=0,shipLoadouts=0,currentSectorIndex=7),
                      2:dict(stats=20,shipData=30),10:dict(SK=11,mercenaries=50),20:dict(tradeMod=0),
                      30:dict(shipModelID=7,cargo=40),31:dict(shipModelID=8,cargo=45),32:dict(shipModelID=9,cargo=46),
                      51:dict(shipData=31,name=0,alive=True,sectorIndex=7,hangarDocked=False,dockedStationID=-1),
                      52:dict(shipData=32,name=0,alive=True,sectorIndex=99,hangarDocked=True,dockedStationID=3)}
        self.lists={40:[41,42],45:[47,48,49,60],46:[61],50:[51,52]}
        def cargo(q,location=-1,typ=3):return dict(itemType=typ,itemID=1,rarity=1,qnt=q,stockStationID=location,extraData=0)
        self.objects.update({41:cargo(10),42:cargo(20,-2),47:cargo(30),48:cargo(20,-2),
                             49:cargo(999,100),60:cargo(1,typ=5),61:cargo(5)})
    def player(self,root):return 3,2
    def get(self,obj,name):return self.objects[obj][name]
    def selected(self,obj,names):return {name:self.objects[obj][name] for name in names if name in self.objects[obj]}
    def values(self,obj):return copy.deepcopy(self.objects[obj])
    def list(self,obj,*args):return list(self.lists.get(obj,[]))
    def array(self,obj,*args):return [0]*49
    def string(self,obj):return 'Captain'


class FleetCargoTests(unittest.TestCase):
    catalog=dict(resale_skill_mod=.01,goods={'1':dict(name='Iron',base_price=90)},ships={})

    def read(self,mono):
        class Catalog:data=self.catalog
        class Connection:
            root=99;catalog=Catalog()
        connection=Connection();connection.mono=mono
        data=example();data['sale_quotes']=[dict(station_id=1,item_id=1,rarity=1,extra=False,mode=-1,price=100)]
        with patch.object(SaleReader,'game_data',return_value=1),patch('trading.TradeReader.read',return_value=data):
            return SaleReader(connection).read()

    def test_combined_fleet_and_stash_have_correct_sale_value(self):
        data=self.read(FleetMono());item=data['inventory'][0]
        self.assertEqual((item['loaded'],item['active_loaded'],item['fleet_loaded'],item['stash']),(45,10,35,20))
        self.assertEqual(len(data['fleet_ships']),3)
        self.assertEqual(sum(s['quantity'] for s in item['sources']),65)
        self.assertEqual(calculate_sales(data,{},False)[0]['proceeds'],6500)
        self.assertFalse(calculate_sales(data,{'3:1:1':'Iron'},True))

    def test_distinct_hangar_and_remote_ships_are_included(self):
        data=self.read(FleetMono())
        sources=data['inventory'][0]['sources']
        self.assertEqual([s['quantity'] for s in sources if not s['active']],[30,5])
        self.assertTrue(any(s.get('hangarDocked') for s in data['fleet_ships']))

    def test_aliases_of_active_ship_and_duplicate_members_do_not_double_count(self):
        mono=FleetMono();mono.objects[53]=dict(shipData=30,alive=True)
        mono.lists[50]+=[51,53]
        data=self.read(mono)
        self.assertEqual(data['inventory'][0]['loaded'],45)
        self.assertEqual(data['inventory'][0]['stash'],20)
        self.assertEqual(len(data['fleet_ships']),3)

    def test_shared_cargo_lists_and_item_objects_are_counted_once(self):
        mono=FleetMono();mono.objects[32]['cargo']=45
        self.assertEqual(self.read(mono)['inventory'][0]['loaded'],40)
        mono=FleetMono();mono.lists[45].append(41)
        self.assertEqual(self.read(mono)['inventory'][0]['loaded'],45)

    def test_dead_ships_are_not_included(self):
        mono=FleetMono();mono.objects[51]['alive']=False
        self.assertEqual(self.read(mono)['inventory'][0]['loaded'],15)

    def test_membership_changes_fail_instead_of_returning_partial_totals(self):
        mono=FleetMono();snapshot=FleetCargoSnapshot(mono,10,30,self.catalog)
        mono.lists[50].pop()
        with self.assertRaises(MemoryUnavailable):snapshot.validate()

    def test_cargo_changes_and_ship_swaps_are_detected(self):
        mono=FleetMono();snapshot=FleetCargoSnapshot(mono,10,30,self.catalog)
        mono.objects[47]['qnt']+=1
        with self.assertRaises(MemoryUnavailable):snapshot.validate()
        mono=FleetMono();snapshot=FleetCargoSnapshot(mono,10,30,self.catalog)
        mono.objects[51]['shipData']=32
        with self.assertRaises(MemoryUnavailable):snapshot.validate()


if __name__=='__main__':unittest.main()
