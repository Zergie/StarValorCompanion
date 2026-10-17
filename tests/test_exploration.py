import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cli
from exploration import ExplorationReader,sector_progress,summarize_sectors,filter_sectors
from reader import MemoryUnavailable
from territory_ui import fit_view


def sector(**changes):
    value=dict(discovered=True,spotted=True,generated=True,fowXtiles=9,fowYtiles=9,x=0,y=0,factionControl=1)
    value.update(changes);return value


class ExplorationMono:
    def __init__(self):
        self.objects={1:dict(sectors=2,currentSectorIndex=0),10:dict(sector(),fow=20,name=30,level=1),
                      11:dict(sector(discovered=False,spotted=False,x=50,y=50),fow=0,name=31,level=50),20:dict(_size=50)}
    def player(self,root):return 98,99
    def get(self,obj,name):return self.objects[obj][name]
    def selected(self,obj,names):return {name:self.objects[obj][name] for name in names if name in self.objects[obj]}
    def list(self,obj,*args,**kwargs):return [10,11]
    def string(self,obj):return {30:'Home',31:'Secret'}[obj]


def read_exploration(mono):
    class Stop:
        def is_set(self):return False
    class Memory:pid=123
    class Connection:stop=Stop();root=90;memory=Memory()
    connection=Connection();connection.mono=mono
    with patch.object(ExplorationReader,'game_data',return_value=1):return ExplorationReader(connection).read()


class ExplorationTests(unittest.TestCase):
    def test_game_fog_formula_and_full_completion(self):
        row=sector_progress(sector(),25,[0,0]);self.assertEqual(row['explored_percent'],75)
        self.assertFalse(row['fully_explored'])
        self.assertTrue(sector_progress(sector(),0,[0,0])['fully_explored'])

    def test_sector_ownership_is_read_without_inventing_unknown_control(self):
        for control,name in [(-1,'Unclaimed'),(0,'Independent'),(3,'Red Skull'),(6,'Technomancers'),(None,'Unknown control')]:
            row=sector_progress(sector(factionControl=control),0,[0,0])
            self.assertEqual((row['faction_id'],row['faction']),(control,name))
        self.assertEqual(read_exploration(ExplorationMono())['sectors'][0]['faction'],'PMC')

    def test_map_fits_negative_coordinates_and_empty_or_single_sector(self):
        self.assertEqual(fit_view([],400,200),(0,0,20))
        x,y,scale=fit_view([[-30,-20],[10,40]],400,200)
        self.assertEqual((x,y),(-10,10));self.assertGreater(scale,0)
        for sx,sy in [(-30,-20),(10,40)]:
            self.assertLess(abs(sx-x)*scale,200);self.assertLess(abs(sy-y)*scale,100)
        self.assertEqual(fit_view([[8,9]],400,200)[:2],(8,9))

    def test_search_matches_faction(self):
        row=sector_progress(sector(factionControl=3),0,[0,0])
        self.assertEqual(filter_sectors([row],'red skull'),[row])

    def test_null_and_ungenerated_fog_do_not_count_as_complete(self):
        for record,remaining in [(sector(),None),(sector(generated=False),0),(sector(discovered=False),0),(sector(),101)]:
            row=sector_progress(record,remaining,[0,0])
            self.assertFalse(row['fully_explored']);self.assertIsNone(row['explored_percent'])

    def test_summary_includes_uncharted_counts_but_avoids_false_completion(self):
        rows=[sector_progress(sector(),0,[0,0]),sector_progress(sector(),50,[0,0]),
              sector_progress(sector(),None,[0,0]),sector_progress(sector(discovered=False),None,[0,0]),
              sector_progress(sector(discovered=False,spotted=False),None,[0,0])]
        summary=summarize_sectors(rows)
        self.assertEqual((summary['total'],summary['visited'],summary['fully_explored'],summary['known']),(5,3,1,4))
        self.assertEqual((summary['unvisited'],summary['uncharted'],summary['unfinished'],summary['unknown_fog']),(2,1,2,1))
        self.assertEqual(summary['fog_cleared_percent'],75)

    def test_empty_galaxy_does_not_divide_by_zero(self):
        self.assertEqual(summarize_sectors([])['fog_cleared_percent'],0)

    def test_reader_hides_uncharted_names_and_coordinates(self):
        result=read_exploration(ExplorationMono())
        self.assertEqual(result['summary']['total'],2);self.assertEqual(len(result['sectors']),1)
        self.assertEqual(result['sectors'][0]['name'],'Home')
        self.assertNotIn('Secret',json.dumps(result));self.assertNotIn('[50, 50]',json.dumps(result))

    def test_sector_change_during_read_is_detected(self):
        mono=ExplorationMono();get=mono.get
        def changed(obj,name):return 1 if (obj,name)==(1,'currentSectorIndex') else get(obj,name)
        mono.get=changed
        with self.assertRaises(MemoryUnavailable):read_exploration(mono)

    def test_cli_filters_known_sectors_without_changing_totals(self):
        data=read_exploration(ExplorationMono())
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.local') as folder:
            path=Path(folder)/'exploration.json';path.write_text(json.dumps(data),encoding='utf-8')
            args=cli.parser().parse_args(['exploration','--input',str(path),'--state','complete'])
            result=cli.execute(args)
            self.assertEqual(result['summary']['total'],2);self.assertEqual(result['sectors'],[])
        self.assertEqual(len(filter_sectors(data['sectors'],'Home',state='incomplete')),1)


if __name__=='__main__':unittest.main()
