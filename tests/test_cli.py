import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cli
from reader import Cancelled, MemoryUnavailable
from test_selling import example


class CliTests(unittest.TestCase):
    def setUp(self):
        local=Path(__file__).resolve().parents[1]/'.local';local.mkdir(exist_ok=True)
        self.folder=tempfile.TemporaryDirectory(dir=local)
        self.directory=Path(self.folder.name)
        self.state=patch.object(cli,'STATE_DIR',self.directory);self.state.start()
    def tearDown(self):self.state.stop();self.folder.cleanup()
    def invoke(self,*args):
        stdout=io.StringIO();stderr=io.StringIO()
        with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):code=cli.main(list(args))
        return code,json.loads(stdout.getvalue()) if stdout.getvalue() else None,stderr.getvalue()
    def snapshot(self,data):
        path=self.directory/'snapshot.json';path.write_text(json.dumps(data),encoding='utf-8');return str(path)

    def test_sale_export_and_offline_replanning_do_not_change_preferences(self):
        source=self.snapshot(example());output=self.directory/'raw.json'
        code,_,_=self.invoke('sell','--input',source,'--raw','--output',str(output))
        self.assertEqual(code,0)
        code,result,_=self.invoke('sell','--input',str(output),'--allow-discard')
        self.assertEqual(code,0);self.assertEqual(result['data']['plans'][0]['proceeds'],4500)
        self.assertFalse(self.invoke('preferences')[1]['data']['allow_discard'])
        self.assertEqual(result['data']['plans'][0]['lines'][0]['status'],'sell')

    def test_saved_exclusions_are_idempotent_and_include_is_temporary(self):
        self.invoke('exclude','add','3:1:1','--name','Iron');self.invoke('exclude','add','3:1:1','--name','Iron')
        self.assertEqual(self.invoke('exclude')[1]['data']['exclusions'],{'3:1:1':'Iron'})
        source=self.snapshot(example())
        self.assertEqual(self.invoke('sell','--input',source)[1]['data']['plans'][0]['proceeds'],100)
        self.assertEqual(self.invoke('sell','--input',source,'--include','3:1:1')[1]['data']['plans'][0]['proceeds'],3100)
        self.assertIn('3:1:1',self.invoke('exclude')[1]['data']['exclusions'])
        self.invoke('exclude','remove','3:1:1');self.invoke('exclude','remove','3:1:1')
        self.assertEqual(self.invoke('exclude')[1]['data']['exclusions'],{})

    def test_preference_persists_and_sale_uses_it(self):
        self.invoke('preferences','--allow-discard')
        self.assertTrue(self.invoke('preferences')[1]['data']['allow_discard'])
        result=self.invoke('sell','--input',self.snapshot(example()))[1]['data']
        self.assertEqual(result['plans'][0]['proceeds'],4500)
        self.invoke('preferences','--no-allow-discard')
        self.assertFalse(self.invoke('preferences')[1]['data']['allow_discard'])

    def test_fleet_preference_and_temporary_override(self):
        data=example()
        data['inventory'][0].update(active_loaded=2,fleet_loaded=8)
        data['inventory'][1].update(active_loaded=0,fleet_loaded=5)
        source=self.snapshot(data)
        self.invoke('preferences','--no-include-fleet')
        result=self.invoke('sell','--input',source)[1]['data']
        self.assertFalse(result['include_fleet'])
        self.assertEqual(result['plans'][0]['proceeds'],3300)
        result=self.invoke('sell','--input',source,'--include-fleet')[1]['data']
        self.assertEqual(result['plans'][0]['proceeds'],3100)
        self.assertFalse(self.invoke('preferences')[1]['data']['include_fleet'])

    def test_inventory_keeps_absent_exclusion_and_resource_stash(self):
        self.invoke('exclude','add','3:99:1','--name','Keep this')
        data=self.invoke('inventory','--input',self.snapshot(example()))[1]['data']
        self.assertTrue(data['items'][0]['excluded']);self.assertEqual(data['items'][0]['loaded'],0)
        self.assertEqual(next(i for i in data['items'] if i['name']=='Iron')['stash'],20)

    def test_ship_section_search_and_all_export(self):
        source=self.snapshot(dict(summary={'credits':100},equipment=[{'name':'Drone'},{'name':'Reactor'}],memory=[]))
        result=self.invoke('ship','--input',source,'--section','equipment','--search','drone')[1]['data']
        self.assertEqual(result['equipment'],[{'name':'Drone'}])
        self.assertIn('summary',self.invoke('ship','--input',source,'--section','all')[1]['data'])

    def test_buy_numeric_sort_search_and_limit(self):
        offers=[dict(name='Drone',station='Workshop',station_id=i,category='Equipment',quality='Blue',price=p,distance=i) for i,p in enumerate([100,20,None])]
        source=self.snapshot(dict(purchase_offers=offers))
        result=self.invoke('buy','--input',source,'--search','drone blue','--sort','price','--limit','1')[1]['data']
        self.assertEqual(result['total'],3);self.assertEqual(result['items'][0]['price'],20)

    def test_gui_export_trade_wrapper_and_threshold(self):
        source=self.snapshot(dict(trading=dict(routes=[{'good':'Iron','profit':100},{'good':'Gold','profit':3000}])))
        result=self.invoke('trades','--input',source,'--min-profit','1000')[1]['data']
        self.assertEqual(result['routes'][0]['good'],'Gold')

    def test_errors_do_not_produce_success_json(self):
        code,data,stderr=self.invoke('sell','--input',self.snapshot({}))
        self.assertEqual(code,1);self.assertIsNone(data);self.assertIn('missing',json.loads(stderr)['error'])
        self.assertEqual(self.invoke('ship','--input',self.snapshot({}),'--fresh')[0],1)
        with patch.object(cli,'read_snapshot',side_effect=MemoryUnavailable('Load a save.')):
            self.assertEqual(self.invoke('ship')[0],2)
        with patch.object(cli,'read_snapshot',side_effect=Cancelled('Read timed out.')):
            self.assertEqual(self.invoke('ship')[0],124)

    def test_gui_arguments_without_launching_a_window(self):
        with patch.object(cli.subprocess,'Popen') as spawn:
            spawn.return_value.pid=123
            result=self.invoke('gui','--tab','Overview','--paused','--keep-on-top')[1]['data']
        self.assertEqual(result['pid'],123)
        arguments=spawn.call_args.args[0]
        self.assertIn('--paused',arguments);self.assertIn('Overview',arguments);self.assertIn('--keep-on-top',arguments)


if __name__=='__main__':unittest.main()
