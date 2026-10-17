import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cli
from personnel import PersonnelReader,recruitment_terms,filter_personnel
from reader import MemoryUnavailable
from trading import station_accessible


class PersonnelMono:
    def __init__(self):
        self.objects={1:dict(stationList=2,sectors=3,currentSectorIndex=0,character=4,crew=5,customNames=0),
                      4:dict(SK=6,rep=7),7:dict(value=8),11:dict(x=0,y=0),12:dict(x=3,y=4),
                      20:dict(discovered=True,id=100,ownerID=0,state=0,buildingState=0,factionIndex=0,sectorIndex=1,buildingPlanID=1,modules=29,dockingControl=0),
                      30:dict(crewForHireIDs=40),31:dict(crewForHireIDs=0),
                      71:dict(id=1001,aiChar=72,rarity=2,skills=73),72:dict(name=80,nickname=81,level=10),
                      74:dict(ID=0,value=6000,skillBonus=76),75:dict(ID=1,value=0,skillBonus=0),77:dict(level=2)}
        self.lists={2:[20],3:[11,12],5:[71],29:[30,31],40:[1001,999,1001],73:[74,75],76:[77]}
    def get(self,obj,name):return self.objects[obj][name]
    def selected(self,obj,names):return {n:self.objects[obj][n] for n in names if n in self.objects[obj]}
    def list(self,obj,*args,**kwargs):return list(self.lists.get(obj,[]))
    def array(self,obj,*args):
        if obj==8:return [0]*7
        values=[0]*49;values[45]=2;return values
    def player(self,root):return 98,99
    def object_class(self,obj):return 'SM_Academy'
    def class_name(self,klass):return klass
    def string(self,obj):return {0:'',80:'Morgan Smith',81:'Morgan'}[obj]


def read_personnel(mono):
    class Stop:
        def is_set(self):return False
    class Catalog:data=dict(stations={'1':'Academy Station'},recruitment_skill_mod=.1)
    class Connection:root=90;catalog=Catalog();stop=Stop()
    connection=Connection();connection.mono=mono;connection.memory=SimpleNamespace(pid=123)
    with patch.object(PersonnelReader,'game_data',return_value=1):return PersonnelReader(connection).read()


class PersonnelTests(unittest.TestCase):
    def test_hire_price_and_requirement_include_skill_bonus_and_recruitment_discount(self):
        cost,requirement=recruitment_terms(2,10,[dict(rank=2,bonus_levels=2),dict(rank=0,bonus_levels=0)],2,.1)
        self.assertAlmostEqual(cost,3432)
        self.assertEqual(requirement,10)

    def test_academy_without_market_is_included_and_unloaded_roster_is_counted(self):
        data=read_personnel(PersonnelMono())
        self.assertEqual((data['academies'],data['unloaded_academies']),(2,1))
        self.assertEqual(len(data['personnel']),2)
        recruit=next(r for r in data['personnel'] if r['crew_id']==1001)
        self.assertEqual((recruit['name'],recruit['distance'],recruit['quality']),('Morgan',5,'Green'))
        self.assertAlmostEqual(recruit['price'],3432)
        self.assertEqual(recruit['skills'][0]['role'],'Engineer')
        unknown=next(r for r in data['personnel'] if r['crew_id']==999)
        self.assertIsNone(unknown['price'])

    def test_station_access_excludes_hostile_owned_and_destroyed_stations(self):
        mono=PersonnelMono();station=mono.objects[20]
        self.assertTrue(station_accessible(mono,station,[0]*7))
        for key,value in [('ownerID',1),('state',1),('buildingState',1)]:
            self.assertFalse(station_accessible(mono,dict(station,**{key:value}),[0]*7))
        self.assertFalse(station_accessible(mono,dict(station,factionIndex=1),[0,-1000]))
        mono.objects.update({91:dict(aisc=92),92:dict(ss=93),93:dict(ffSys=94),94:dict(hostileToPlayer=True)})
        self.assertFalse(station_accessible(mono,dict(station,dockingControl=91),[0]*7))

    def test_search_roles_and_sort_missing_cost_last(self):
        data=read_personnel(PersonnelMono())
        self.assertEqual(len(filter_personnel(data['personnel'],'engineer green')),1)
        self.assertIsNone(filter_personnel(data['personnel'],sort='price',descending=True)[-1]['price'])

    def test_roster_change_during_read_is_detected(self):
        mono=PersonnelMono();original=mono.list;reads=0
        def read_list(obj,*args,**kwargs):
            nonlocal reads
            if obj==40:
                reads+=1
                if reads>1:return []
            return original(obj,*args,**kwargs)
        mono.list=read_list
        with self.assertRaises(MemoryUnavailable):read_personnel(mono)

    def test_cli_can_search_offline_recruitment_snapshot(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.local') as folder:
            path=Path(folder)/'personnel.json';path.write_text(json.dumps(read_personnel(PersonnelMono())),encoding='utf-8')
            args=cli.parser().parse_args(['personnel','--input',str(path),'--search','engineer','--sort','price'])
            data=cli.execute(args)
            self.assertEqual(data['total'],1)
            self.assertEqual(data['personnel'][0]['name'],'Morgan')


if __name__=='__main__':unittest.main()
