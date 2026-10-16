import json
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trading import market_price,calculate_routes,cargo_capacity
from window_state import fit,load,save


def offer(station,coords,price,mode=0,stock=100,weight=1,buy_limit=999999,item=1):
    return dict(station_id=station,coordinates=coords,position=[0,0],price=price,mode=mode,
                available=stock,weight=weight,buy_limit=buy_limit,item_id=item,name='Iron')


class TradingTests(unittest.TestCase):
    def setUp(self):self.current=dict(coordinates=[0,0],position=[0,0])
    def test_station_direction_and_stock_pricing(self):
        item=dict(trade_quantity=100,base_price=100,variation=.2)
        self.assertAlmostEqual(market_price(item,1,100),80)
        self.assertAlmostEqual(market_price(item,-1,100),104)
        self.assertAlmostEqual(market_price(item,-1,0),128.8)
        self.assertEqual(market_price(item,0,10,123),123)
        self.assertIsNone(market_price(item,0,10,-1))
    def test_nearest_means_entire_route_not_only_buy_station(self):
        a=offer(1,[1,0],10,1,item=1);b=offer(2,[100,0],20,-1,item=1)
        c=offer(3,[5,0],10,1,item=2);d=offer(4,[6,0],20,-1,item=2)
        routes=calculate_routes([a,b,c,d],self.current,1000,100)
        self.assertEqual(routes[0]['item_id'],2)
        self.assertEqual(routes[0]['total_distance'],6)
    def test_each_quantity_limit_is_enforced(self):
        buy=offer(1,[1,0],10,1,stock=50,weight=2);sell=offer(2,[2,0],20,-1,buy_limit=30)
        self.assertEqual(calculate_routes([buy,sell],self.current,1000,5)[0]['quantity'],2)
        self.assertEqual(calculate_routes([buy,sell],self.current,15,1000)[0]['quantity'],1)
        self.assertEqual(calculate_routes([buy,sell],self.current,1000,1000)[0]['quantity'],30)
        buy['available']=4
        self.assertEqual(calculate_routes([buy,sell],self.current,1000,1000)[0]['quantity'],4)
    def test_no_self_trade_loss_or_impossible_load(self):
        buy=offer(1,[0,0],10,1);sell=offer(1,[0,0],20,-1)
        self.assertFalse(calculate_routes([buy,sell],self.current,1000,100))
        sell['station_id']=2;sell['price']=9
        self.assertFalse(calculate_routes([buy,sell],self.current,1000,100))
        sell['price']=20
        self.assertFalse(calculate_routes([buy,sell],self.current,1000,0))
        self.assertEqual(calculate_routes([buy,sell],self.current,1000,0,{1:3})[0]['quantity'],3)
    def test_cargo_optimization_stash_and_reserved_item_capacity(self):
        cargo=[dict(stockStationID=-1,itemID=1,itemType=3,qnt=20),
               dict(stockStationID=-2,itemID=1,itemType=3,qnt=999),
               dict(stockStationID=-1,itemID=23,itemType=3,qnt=10)]
        goods={'1':dict(weight=1,kind=2),'23':dict(weight=.5,kind=4)}
        used,extra=cargo_capacity(cargo,goods,{}, {},[],True,{23:3})
        self.assertEqual(used,12)
        self.assertEqual(extra[23],0)
        used,extra=cargo_capacity(cargo,goods,{}, {},[],False,{23:8})
        self.assertEqual(used,20);self.assertEqual(extra[23],3)


class WindowStateTests(unittest.TestCase):
    def test_negative_monitor_coordinates_are_preserved(self):
        bounds=dict(x=-1700,y=80,width=1000,height=800)
        self.assertEqual(fit(bounds,(-1920,0,0,1080)),bounds)
    def test_disconnected_monitor_position_is_moved_into_work_area(self):
        b=fit(dict(x=7000,y=3000,width=1140,height=840),(0,0,1920,1040))
        self.assertGreaterEqual(b['x'],0);self.assertGreaterEqual(b['y'],0)
        self.assertLessEqual(b['x']+b['width'],1920)
        self.assertLessEqual(b['y']+b['height'],1040)
    def test_state_round_trip_and_bad_file_fallback(self):
        test_root=Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(dir=test_root) as tmp:
            self.assertTrue(Path(tmp).resolve().is_relative_to(test_root))
            p=Path(tmp)/'window.json';state=dict(bounds=dict(x=-1200,y=100,width=1000,height=800),maximized=True)
            save(p,state);self.assertEqual(load(p),state)
            p.write_text('{broken');self.assertEqual(load(p),{})
            p.write_text(json.dumps(dict(bounds=dict(x=0,y=0,width=-10,height=800))))
            self.assertEqual(load(p),{})


if __name__=='__main__':unittest.main()
