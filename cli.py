"""One-shot JSON interface to the companion's existing readers and calculations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading

from buying import BuyReader, filter_offers
from personnel import PersonnelReader,filter_personnel
from exploration import ExplorationReader,filter_sectors
from reader import Cancelled, MemoryUnavailable
from selling import Exclusions, SaleReader, calculate_sales, inventory_rows, load_allow_discard, save_allow_discard, load_include_fleet, save_include_fleet
from telemetry import APP_DIR, STATE_DIR, Catalog, Connection
from trading import TradeReader

TABS=['Overview','Equipment','Ship modifications','Trading','Sell cargo','Buy items','Personnel','Exploration','Memory map']


def nonnegative(value):
    number=float(value)
    if not 0<=number<float('inf'):raise argparse.ArgumentTypeError('Must be a finite nonnegative number.')
    return number


def positive(value):
    number=nonnegative(value)
    if number==0:raise argparse.ArgumentTypeError('Must be greater than zero.')
    return number


def item_key(value):
    try:
        parts=[int(p) for p in value.split(':')]
        if len(parts)!=3 or parts[0] not in (1,2,3,4) or min(parts[1:])<0:raise ValueError()
    except ValueError:raise argparse.ArgumentTypeError('Use TYPE:ID:QUALITY (types 1–4; crew is ignored).')
    return ':'.join(map(str,parts))


def parser():
    root=argparse.ArgumentParser(description='Star Valor Companion: one-shot JSON reads and local preferences. No game writes.')
    commands=root.add_subparsers(dest='command',required=True)
    common=argparse.ArgumentParser(add_help=False)
    common.add_argument('--output',type=Path,help='Also save the JSON result to this file.')
    live=argparse.ArgumentParser(add_help=False,parents=[common])
    live.add_argument('--input',type=Path,help='Use an exported reader snapshot instead of live game memory.')
    live.add_argument('--fresh',action='store_true',help='Rediscover the live player instead of using the connection cache.')
    live.add_argument('--timeout',type=positive,default=120,help='Cancel discovery/scans after this many seconds (default 120).')
    live.add_argument('--raw',action='store_true',help='Return the reusable raw reader snapshot before filtering.')
    ship=commands.add_parser('ship',parents=[live],help='Overview, equipment, modifications, crew, or memory map.')
    ship.add_argument('--section',choices=['summary','equipment','modifications','crew','memory','all'],default='summary')
    ship.add_argument('--search',default='',help='Filter equipment, modification, crew, or memory rows.')
    inventory=commands.add_parser('inventory',parents=[live],help='All fleet cargo, resource stash, item sources, and saved exclusions.')
    trades=commands.add_parser('trades',parents=[live],help='Nearest arbitrage routes.')
    trades.add_argument('--min-profit',type=nonnegative,default=1000)
    trades.add_argument('--search',default='')
    trades.add_argument('--limit',type=int,default=100,help='Maximum results; 0 for all retained routes.')
    sell=commands.add_parser('sell',parents=[live],help='Best single-station sale plans, including discard/review lines.')
    sell.add_argument('--include-fleet',action=argparse.BooleanOptionalAction,default=None)
    sell.add_argument('--allow-discard',action=argparse.BooleanOptionalAction,default=None,help='Override saved preference for this invocation only.')
    sell.add_argument('--exclude',type=item_key,action='append',default=[],help='Temporarily exclude an item key; repeatable.')
    sell.add_argument('--include',type=item_key,action='append',default=[],help='Temporarily include a saved exclusion; repeatable.')
    sell.add_argument('--limit',type=int,default=0)
    buy=commands.add_parser('buy',parents=[live],help='All available purchase listings.')
    buy.add_argument('--search',default='')
    buy.add_argument('--sort',choices=['distance','price','quantity','name','station','category','quality'],default='distance')
    buy.add_argument('--descending',action='store_true')
    buy.add_argument('--limit',type=int,default=0)
    personnel=commands.add_parser('personnel',parents=[live],help='Available academy recruits, skills and hiring costs.')
    personnel.add_argument('--search',default='')
    personnel.add_argument('--sort',choices=['distance','price','name','level','requirement'],default='distance')
    personnel.add_argument('--descending',action='store_true')
    personnel.add_argument('--limit',type=int,default=0)
    exploration=commands.add_parser('exploration',parents=[live],help='Galaxy exploration totals and known-sector progress.')
    exploration.add_argument('--search',default='')
    exploration.add_argument('--state',choices=['all','visited','incomplete','complete','unvisited'],default='all')
    exclude=commands.add_parser('exclude',parents=[common],help='List/add/remove persistent Sell cargo item exclusions.')
    exclude.add_argument('action',choices=['list','add','remove'],nargs='?',default='list')
    exclude.add_argument('key',type=item_key,nargs='?')
    exclude.add_argument('--name',help='Display name for an added exclusion (optional).')
    settings=commands.add_parser('preferences',parents=[common],help='Read or change the saved discard checkbox.')
    settings.add_argument('--include-fleet',action=argparse.BooleanOptionalAction,default=None)
    settings.add_argument('--allow-discard',action=argparse.BooleanOptionalAction,default=None)
    catalog=commands.add_parser('catalog',parents=[common],help='Search the installed item/name catalog without the game.')
    catalog.add_argument('--category',choices=['goods','equipment','ships','modifications','stations','all'],default='all')
    catalog.add_argument('--search',default='')
    gui=commands.add_parser('gui',parents=[common],help='Open the native app; window controls remain in the GUI.')
    gui.add_argument('--tab',choices=TABS,default='Overview')
    gui.add_argument('--paused',action='store_true')
    gui.add_argument('--keep-on-top',action='store_true')
    return root


def matches(row,query):
    text=json.dumps(row,ensure_ascii=False).casefold()
    return all(term in text for term in query.casefold().split())


def read_snapshot(args):
    if args.input:
        if args.fresh:raise ValueError('--fresh cannot be used with --input.')
        data=json.loads(args.input.read_text(encoding='utf-8-sig'))
        # Accept the CLI envelope as well as raw reader snapshots and GUI ship exports.
        if isinstance(data,dict) and data.get('command') and 'data' in data:data=data['data']
        if not isinstance(data,dict):raise ValueError('Snapshot must be a JSON object.')
        return data
    stop=threading.Event()
    def progress(message,percent):print(f'{percent:.0%} {message}',file=sys.stderr)
    connection=Connection(progress,stop)
    timer=threading.Timer(args.timeout,stop.set);timer.daemon=True;timer.start()
    try:
        if args.fresh:connection.connect(fresh=True)
        if args.command=='ship':data=connection.read()
        else:
            reader={'personnel':PersonnelReader,'exploration':ExplorationReader,'buy':BuyReader,'sell':SaleReader,'inventory':SaleReader,'trades':TradeReader}[args.command]
            data=reader(connection).read()
        if stop.is_set():raise Cancelled('Read timed out.')
        return data
    finally:
        timer.cancel();connection.close()


def metadata(data):
    return {key:data[key] for key in ('read_at','pid','source','current','stations','unpriced_markets','fleet_ships') if key in data}


def require(data,*keys):
    missing=[k for k in keys if k not in data]
    if missing:raise ValueError('Snapshot is missing '+', '.join(missing)+'. Use the matching command with --raw to export a reusable reader snapshot.')


def execute(args):
    settings_path=STATE_DIR/'sale-settings.json'
    exclusions=Exclusions(STATE_DIR/'sale-exclusions.json')
    if args.command=='preferences':
        if args.allow_discard is not None:save_allow_discard(settings_path,args.allow_discard)
        if args.include_fleet is not None:save_include_fleet(settings_path,args.include_fleet)
        return dict(allow_discard=load_allow_discard(settings_path),include_fleet=load_include_fleet(settings_path))
    if args.command=='exclude':
        if args.action!='list' and args.key is None:raise ValueError('An item key is required for add/remove. Use inventory to find keys.')
        if args.action=='add' and args.key not in exclusions.items:
            exclusions.toggle(args.key,args.name or f'Item {args.key}')
        elif args.action=='remove' and args.key in exclusions.items:exclusions.toggle(args.key,exclusions.items[args.key])
        return dict(exclusions=exclusions.items)
    if args.command=='catalog':
        catalog=Catalog().data;rows=[]
        categories=['goods','equipment','ships','modifications','stations'] if args.category=='all' else [args.category]
        for category in categories:
            for item,record in catalog[category].items():
                row=dict(category=category,id=item,details=record)
                if matches(row,args.search):rows.append(row)
        return dict(build=catalog.get('build'),items=rows,count=len(rows))
    if args.command=='gui':
        python=Path(sys.executable).with_name('pythonw.exe')
        command=[str(python if python.exists() else sys.executable),str(APP_DIR/'app.py'),'--tab',args.tab]
        if args.paused:command.append('--paused')
        if args.keep_on_top:command.append('--keep-on-top')
        process=subprocess.Popen(command,cwd=APP_DIR,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return dict(pid=process.pid,tab=args.tab)
    if getattr(args,'limit',0)<0:raise ValueError('--limit must be zero or greater.')
    data=read_snapshot(args)
    if args.raw:return data
    result=metadata(data)
    if args.command=='ship':
        if args.section=='all':return data
        require(data,args.section)
        value=data[args.section]
        if args.search and isinstance(value,list):value=[r for r in value if matches(r,args.search)]
        result[args.section]=value
    elif args.command=='inventory':
        require(data,'inventory')
        result.update(items=inventory_rows(data['inventory'],exclusions.items),exclusions=exclusions.items,allow_discard=load_allow_discard(settings_path))
    elif args.command=='buy':
        require(data,'purchase_offers')
        rows=filter_offers(data['purchase_offers'],args.search,args.sort,args.descending)
        result.update(items=rows[:args.limit] if args.limit else rows,total=len(rows))
    elif args.command=='personnel':
        require(data,'personnel')
        rows=filter_personnel(data['personnel'],args.search,args.sort,args.descending)
        result.update(personnel=rows[:args.limit] if args.limit else rows,total=len(rows),academies=data.get('academies',0),unloaded_academies=data.get('unloaded_academies',0))
    elif args.command=='exploration':
        require(data,'summary','sectors')
        result.update(summary=data['summary'],sectors=filter_sectors(data['sectors'],args.search,args.state))
    elif args.command=='trades':
        if 'routes' not in data and isinstance(data.get('trading'),dict):data=data['trading'];result=metadata(data)
        require(data,'routes')
        rows=[r for r in data['routes'] if r['profit']>=args.min_profit and matches(r,args.search)]
        result.update(routes=rows[:args.limit] if args.limit else rows,total=len(rows))
    elif args.command=='sell':
        require(data,'inventory','quotes','sale_stations','current')
        excluded=dict(exclusions.items)
        for key in args.include:excluded.pop(key,None)
        for key in args.exclude:excluded[key]='Temporary exclusion'
        discard=load_allow_discard(settings_path) if args.allow_discard is None else args.allow_discard
        include_fleet=load_include_fleet(settings_path) if args.include_fleet is None else args.include_fleet
        plans=calculate_sales(data,excluded,discard,include_fleet)
        for plan in plans:
            plan['lines']=sorted(plan['lines'],key=lambda i:(i['status']!='sell',-i['value'] if i['status']=='sell' else 0))
        result.update(plans=plans[:args.limit] if args.limit else plans,total=len(plans),exclusions=excluded,allow_discard=discard,include_fleet=include_fleet)
    return result


def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    root=parser()
    args=root.parse_args(argv)
    try:
        data=execute(args);result=dict(command=args.command,data=data)
        output=json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)
        if args.output:args.output.write_text(output+'\n',encoding='utf-8')
        print(output)
        return 0
    except (MemoryUnavailable,Cancelled) as exc:
        code=124 if isinstance(exc,Cancelled) else 2
        print(json.dumps(dict(error=str(exc) or 'Read cancelled or timed out.',exit_code=code)),file=sys.stderr)
        return code
    except KeyboardInterrupt:
        print(json.dumps(dict(error='Interrupted.',exit_code=130)),file=sys.stderr);return 130
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps(dict(error=str(exc),exit_code=1)),file=sys.stderr);return 1


if __name__=='__main__':sys.exit(main())
