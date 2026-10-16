"""Star Valor Companion — native Tk desktop dashboard, standard-library only."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from reader import Cancelled, MemoryUnavailable
from telemetry import APP_DIR, STATE_DIR, Connection
from trading import TradeWorker
from selling import SaleReader
from selling_ui import SellingPage
from buying import BuyReader
from buying_ui import BuyingPage
import window_state

BG='#10171f'; PANEL='#19232e'; TEXT='#e5edf5'; MUTED='#93a6b8'; ACCENT='#4ed8b1'
QUALITY={'Grey':'#93a6b8','White':'#e5edf5','Green':'#79d69a','Blue':'#76b8ff','Purple':'#c79dff','Orange':'#ffbd72'}


def number(value,places=1):
    return f'{value:,.{places}f}'


class Worker(threading.Thread):
    def __init__(self,events):
        super().__init__(daemon=True,name='StarValorReader')
        self.events=events; self.stop_event=threading.Event(); self.rescan_event=threading.Event()
        self.pause_event=threading.Event(); self.wake=threading.Event(); self.last_notice=0; self.last_text=''
        self.cancel_discovery=threading.Event()
        self.connection=Connection(self.notify,self.cancel_discovery)
    def notify(self,text,progress=0):
        now=time.monotonic()
        if now-self.last_notice>.15 or text!=self.last_text or progress==1:
            self.events.put(('status',text,progress)); self.last_notice=now; self.last_text=text
    def run(self):
        try:
            while not self.stop_event.is_set():
                if self.pause_event.is_set():
                    self.notify('Paused — live reads stopped',0); self.wake.wait(.5); self.wake.clear(); continue
                try:
                    self.cancel_discovery.clear()
                    if self.rescan_event.is_set():
                        self.rescan_event.clear(); self.connection.close()
                        self.connection.connect(fresh=True)
                    data=self.connection.read()
                    self.events.put(('snapshot',data))
                    delay=1.0
                except Cancelled:
                    self.connection.close()
                    if self.stop_event.is_set(): break
                    continue
                except MemoryUnavailable as exc:
                    self.notify(str(exc),0); delay=3.0
                    if self.connection.memory and not self.connection.memory.alive(): self.connection.close()
                except Exception:
                    logging.exception('Telemetry read failed')
                    self.notify('Could not read telemetry. See .local/companion.log; use Reconnect to retry.',0)
                    self.connection.close(); delay=5.0
                self.wake.wait(delay); self.wake.clear()
        finally: self.connection.close()
    def shutdown(self):
        self.stop_event.set(); self.cancel_discovery.set(); self.wake.set()


class Companion(tk.Tk):
    def __init__(self,start_worker=True,start_paused=False):
        super().__init__()
        self.title('Star Valor Companion'); self.geometry('1140x840'); self.minsize(960,740)
        self.configure(bg=BG)
        self.events=queue.Queue(); self.latest=None; self.worker=None; self.trade_worker=None;self.trade_data=None;self._closed=False
        self.sale_worker=None;self.buy_worker=None
        self._window_path=STATE_DIR/'window.json'
        self._equipment_signature=None; self._mod_signature=None
        self.variables={}; self.metrics={}; self.progress_bars={}
        self._style(); self._build()
        window_state.restore(self,window_state.load(self._window_path))
        self.protocol('WM_DELETE_WINDOW',self.close)
        if start_worker:
            self.worker=Worker(self.events)
            if start_paused:self.worker.pause_event.set();self.pause_button.configure(text='Resume')
            self.worker.start()
            self.trade_worker=TradeWorker(self.events);self.trade_worker.start()
            self.sale_worker=TradeWorker(self.events,SaleReader,'sales','sale_status');self.sale_worker.start()
            self.buy_worker=TradeWorker(self.events,BuyReader,'purchases','buy_status');self.buy_worker.start()
        self._poll_after=self.after(100,self.poll)
    def _style(self):
        style=ttk.Style(self); style.theme_use('clam')
        style.configure('.',font=('Segoe UI',10),background=BG,foreground=TEXT)
        style.configure('TFrame',background=BG)
        style.configure('Panel.TFrame',background=PANEL)
        style.configure('TLabel',background=BG,foreground=TEXT)
        style.configure('Muted.TLabel',foreground=MUTED)
        style.configure('Panel.TLabel',background=PANEL)
        style.configure('PanelMuted.TLabel',background=PANEL,foreground=MUTED)
        style.configure('Title.TLabel',font=('Segoe UI Semibold',24))
        style.configure('CardValue.TLabel',background=PANEL,font=('Segoe UI Semibold',21))
        style.configure('Section.TLabel',background=PANEL,font=('Segoe UI Semibold',12))
        style.configure('TButton',background='#253747',padding=(14,8),borderwidth=0)
        style.map('TButton',background=[('active','#355168'),('disabled',PANEL)],foreground=[('disabled',MUTED)])
        style.configure('TCheckbutton',background=BG,foreground=MUTED)
        style.map('TCheckbutton',background=[('active',BG)])
        style.configure('TNotebook',background=BG,borderwidth=0)
        style.configure('TNotebook.Tab',padding=(20,12),background=BG,foreground=MUTED)
        style.map('TNotebook.Tab',background=[('selected',PANEL)],foreground=[('selected',ACCENT)])
        style.configure('Treeview',background=PANEL,fieldbackground=PANEL,foreground=TEXT,rowheight=31,borderwidth=0)
        style.configure('Treeview.Heading',background='#253240',foreground=MUTED,padding=(8,8),font=('Segoe UI Semibold',10))
        style.map('Treeview',background=[('selected','#30505a')],foreground=[('selected',TEXT)])
        style.configure('Horizontal.TProgressbar',background=ACCENT,troughcolor='#283744',borderwidth=0,thickness=6)
        style.configure('TEntry',fieldbackground=PANEL,foreground=TEXT,insertcolor=TEXT,padding=8)
        style.configure('TSpinbox',fieldbackground=PANEL,foreground=TEXT,background='#253747',arrowcolor=MUTED,padding=5)
        style.map('TSpinbox',fieldbackground=[('!disabled',PANEL)],foreground=[('!disabled',TEXT)])
    def var(self,key,value='—'):
        self.variables[key]=tk.StringVar(value=value); return self.variables[key]
    def _build(self):
        shell=ttk.Frame(self,padding=(26,20)); shell.pack(fill='both',expand=True)
        header=ttk.Frame(shell); header.pack(fill='x')
        title=ttk.Frame(header); title.pack(side='left')
        ttk.Label(title,text='STAR VALOR  /  COMPANION',style='Muted.TLabel').pack(anchor='w')
        ttk.Label(title,textvariable=self.var('ship','Waiting for your ship'),style='Title.TLabel').pack(anchor='w',pady=(3,0))
        actions=ttk.Frame(header); actions.pack(side='right')
        self.topmost=tk.BooleanVar(value=False)
        ttk.Checkbutton(actions,text='Keep on top',variable=self.topmost,command=lambda:self.attributes('-topmost',self.topmost.get())).pack(side='left',padx=10)
        self.pause_button=ttk.Button(actions,text='Pause',command=self.pause); self.pause_button.pack(side='left',padx=4)
        ttk.Button(actions,text='Reconnect',command=self.reconnect).pack(side='left',padx=4)
        self.export_button=ttk.Button(actions,text='Export snapshot',command=self.export,state='disabled'); self.export_button.pack(side='left',padx=4)
        status=ttk.Frame(shell); status.pack(fill='x',pady=(13,5))
        self.status_dot=ttk.Label(status,text='●',foreground=MUTED); self.status_dot.pack(side='left',padx=(0,8))
        ttk.Label(status,textvariable=self.var('status','Connecting to Star Valor…'),style='Muted.TLabel').pack(side='left')
        ttk.Label(status,text='READ ONLY',foreground=ACCENT).pack(side='right')
        self.discovery=ttk.Progressbar(shell,maximum=100); self.discovery.pack(fill='x',pady=(3,14))
        tabs=ttk.Notebook(shell); tabs.pack(fill='both',expand=True);self.tabs=tabs
        overview=ttk.Frame(tabs,padding=(0,18)); tabs.add(overview,text='Overview')
        equipment=ttk.Frame(tabs,padding=(0,18)); tabs.add(equipment,text='Equipment')
        mods=ttk.Frame(tabs,padding=(0,18)); tabs.add(mods,text='Ship modifications')
        trades=ttk.Frame(tabs,padding=(0,18));tabs.add(trades,text='Trading');self.trade_tab=trades
        self.selling_page=SellingPage(tabs,self.request_sales,STATE_DIR/'sale-exclusions.json');tabs.add(self.selling_page,text='Sell cargo')
        self.buying_page=BuyingPage(tabs,self.request_purchases);tabs.add(self.buying_page,text='Buy items')
        memory=ttk.Frame(tabs,padding=(0,18)); tabs.add(memory,text='Memory map')
        self._overview(overview); self._equipment(equipment); self._mods(mods);self._trading(trades);self._memory(memory)
        footer=ttk.Frame(shell); footer.pack(fill='x',pady=(12,0))
        ttk.Label(footer,textvariable=self.var('updated','No live snapshot yet'),style='Muted.TLabel').pack(side='left')
        ttk.Label(footer,text='Ship stats · refresh every second',style='Muted.TLabel').pack(side='right')
        # Reserve the global footer before the expanding notebook consumes space.
        tabs.pack_forget();footer.pack_forget()
        footer.pack(side='bottom',fill='x',pady=(12,0));tabs.pack(fill='both',expand=True)
    def _overview(self,parent):
        cards=ttk.Frame(parent); cards.pack(fill='x')
        for col in range(4): cards.columnconfigure(col,weight=1,uniform='cards')
        for i,(key,label) in enumerate([('hull','HULL'),('energy','ENERGY'),('shield','SHIELDS'),('cargo','CARGO CAPACITY'),
                                         ('credits','CREDITS'),('equipment','EQUIPMENT SPACE'),('weapons','WEAPON SPACE'),('cooling','WEAPON COOLING')]):
            card=ttk.Frame(cards,style='Panel.TFrame',padding=(16,12)); card.grid(row=i//4,column=i%4,sticky='nsew',padx=(0,8) if i%4<3 else 0,pady=(0,8))
            ttk.Label(card,text=label,style='PanelMuted.TLabel',font=('Segoe UI',9)).pack(anchor='w')
            ttk.Label(card,textvariable=self.var(key),style='CardValue.TLabel').pack(anchor='w',pady=(4,4))
            if key in ['hull','energy','shield']:
                bar=ttk.Progressbar(card,maximum=100); bar.pack(fill='x',pady=(4,0)); self.progress_bars[key]=bar
        panels=ttk.Frame(parent); panels.pack(fill='both',expand=True,pady=(8,0))
        for c in range(3): panels.columnconfigure(c,weight=1,uniform='details')
        panel_data=[('Mining',[('mining_speed','Mining speed bonus'),('mining_loot','Mining loot bonus'),('geology','Additional Geology'),('metals','Special-metal bonus')]),
                    ('Drones',[('drone_damage','Damage bonus'),('drone_hp','Extra HP'),('drone_speed','Extra speed'),('drone_auto','Automatic targeting'),('drone_parts','Parts recovery')]),
                    ('Power & movement',[('generation','Energy generation'),('draw','Passive energy draw'),('heat','Reactor heat · raw'),('speed','Speed stat'),('acceleration','Acceleration')])]
        for col,(title,rows) in enumerate(panel_data):
            frame=ttk.Frame(panels,style='Panel.TFrame',padding=16); frame.grid(row=0,column=col,sticky='nsew',padx=(0,8) if col<2 else 0)
            ttk.Label(frame,text=title,style='Section.TLabel').pack(anchor='w',pady=(0,16))
            for key,label in rows:
                line=ttk.Frame(frame,style='Panel.TFrame'); line.pack(fill='x',pady=6)
                ttk.Label(line,text=label,style='PanelMuted.TLabel').pack(side='left')
                ttk.Label(line,textvariable=self.var(key),style='Panel.TLabel',font=('Segoe UI Semibold',10)).pack(side='right')
        ttk.Label(parent,text='Energy generation and passive draw are raw stats; movement, shields and weapon fire add action costs.',style='Muted.TLabel',wraplength=1000).pack(anchor='w',pady=(12,0))
        ttk.Label(parent,textvariable=self.var('crew','Crew efficiencies will appear when a ship is loaded.'),style='Muted.TLabel',wraplength=1000).pack(anchor='w',pady=(6,0))
    def tree(self,parent,columns,widths):
        frame=ttk.Frame(parent); frame.pack(fill='both',expand=True)
        tree=ttk.Treeview(frame,columns=columns,show='headings',selectmode='browse')
        for column,width in zip(columns,widths):
            tree.heading(column,text=column); tree.column(column,width=width,minwidth=min(width,65),stretch=column in ['Part','Effect','Field','Value'])
        scroll=ttk.Scrollbar(frame,orient='vertical',command=tree.yview); tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left',fill='both',expand=True); scroll.pack(side='right',fill='y')
        return tree
    def _equipment(self,parent):
        row=ttk.Frame(parent); row.pack(fill='x',pady=(0,12))
        ttk.Label(row,text='Installed equipment',font=('Segoe UI Semibold',13)).pack(side='left')
        self.search=tk.StringVar(); self.search.trace_add('write',lambda *_:self.render_equipment())
        ttk.Label(row,text='Filter',style='Muted.TLabel').pack(side='right',padx=8)
        ttk.Entry(row,textvariable=self.search,width=28).pack(side='right')
        self.equipment_tree=self.tree(parent,['Part','Quality','Qty','Space each','Tech level'],[430,120,60,110,100])
        for name,color in QUALITY.items(): self.equipment_tree.tag_configure(name,foreground=color)
        ttk.Label(parent,text='Quality colours and model versions are separate. Space is per installed unit. Weapon-space usage is shown on Overview.',style='Muted.TLabel',wraplength=1000).pack(anchor='w',pady=(12,0))
    def _mods(self,parent):
        ttk.Label(parent,text='Active ship modifications',font=('Segoe UI Semibold',13)).pack(anchor='w',pady=(0,12))
        self.mods_tree=self.tree(parent,['Slot','Tier','Effect'],[70,70,780])
        ttk.Label(parent,text='The drone and mining values on Overview already include active modifications and crew effects.',style='Muted.TLabel').pack(anchor='w',pady=(12,0))
    def _memory(self,parent):
        ttk.Label(parent,textvariable=self.var('pointer','No active player reference yet.'),font=('Consolas',10),wraplength=1050).pack(anchor='w',pady=(0,12))
        self.memory_tree=self.tree(parent,['Object','Field','Offset','Address','Type','Value'],[140,200,75,150,85,140])
        ttk.Label(parent,text='Addresses belong to this game session and may change. This app only reads memory.',style='Muted.TLabel').pack(anchor='w',pady=(12,0))
    def _trading(self,parent):
        controls=ttk.Frame(parent);controls.pack(fill='x',pady=(0,12))
        ttk.Label(controls,text='Nearest arbitrage routes',font=('Segoe UI Semibold',13)).pack(side='left')
        ttk.Button(controls,text='Refresh routes',command=self.request_trades).pack(side='right')
        self.minimum_profit=tk.StringVar(value='1000')
        self.minimum_profit.trace_add('write',lambda *_:self.render_trades())
        ttk.Spinbox(controls,from_=0,to=10000000,increment=1000,textvariable=self.minimum_profit,width=10).pack(side='right',padx=(8,14))
        ttk.Label(controls,text='Minimum profit',style='Muted.TLabel').pack(side='right')
        detail=ttk.Frame(parent,style='Panel.TFrame',padding=16);detail.pack(fill='x',pady=(0,12))
        ttk.Label(detail,textvariable=self.var('trade_buy','Click Refresh routes to read live station markets.'),style='Section.TLabel',wraplength=1000).pack(anchor='w')
        ttk.Label(detail,textvariable=self.var('trade_sell',''),style='Panel.TLabel',wraplength=1000).pack(anchor='w',pady=(8,0))
        ttk.Label(detail,textvariable=self.var('trade_profit',''),style='PanelMuted.TLabel',wraplength=1000).pack(anchor='w',pady=(8,0))
        notes=ttk.Frame(parent);notes.pack(side='bottom',fill='x',pady=(10,0))
        ttk.Label(notes,textvariable=self.var('trade_status','Routes use discovered stations with loaded prices. Profits exclude travel costs.'),style='Muted.TLabel',wraplength=1000).pack(anchor='w')
        ttk.Label(notes,text='Nearest = current sector → buy sector → sell sector, using straight-line distances. Stock and prices may change before arrival.',style='Muted.TLabel',wraplength=1000).pack(anchor='w',pady=(6,0))
        self.routes_tree=self.tree(parent,['Good','Qty','Buy at','Sell at','Profit','Route pc'],[130,55,265,265,90,75])
        self.routes_tree.column('Buy at',stretch=True);self.routes_tree.column('Sell at',stretch=True)
        self.routes_tree.bind('<<TreeviewSelect>>',self.select_route)
        self.shown_routes=[]
    def request_trades(self):
        if self.trade_worker:
            if self.worker and self.worker.pause_event.is_set():
                self.variables['trade_status'].set('Paused — resume live reads to refresh trading routes.');return
            self.trade_worker.request.set()
            self.variables['trade_status'].set('Reading live station stock and prices…')
    def request_sales(self):
        if not self.sale_worker:return
        if self.worker and self.worker.pause_event.is_set():
            self.selling_page.status.set('Paused — resume live reads to refresh cargo and prices.');return
        self.sale_worker.request.set()
        self.selling_page.status.set('Reading cargo, resource stash and station prices…')
    def request_purchases(self):
        if not self.buy_worker:return
        if self.worker and self.worker.pause_event.is_set():
            self.buying_page.status.set('Paused — resume live reads to refresh purchase listings.');return
        self.buy_worker.request.set()
        self.buying_page.status.set('Reading station purchase listings, stock and prices…')
    @staticmethod
    def station_label(station):
        x,y=station['coordinates'];return f"{station['station']} [{station['level']}] · ({x}, {y})"
    def render_trades(self):
        if not self.trade_data:return
        try:minimum=max(0,float(self.minimum_profit.get()))
        except ValueError:return
        self.shown_routes=[r for r in self.trade_data['routes'] if r['profit']>=minimum][:100]
        previous=self.routes_tree.selection()
        previous_key=self.routes_tree.item(previous[0],'values')[:4] if previous else None
        self.routes_tree.delete(*self.routes_tree.get_children());selected=None
        for i,r in enumerate(self.shown_routes):
            values=(r['good'],r['quantity'],self.station_label(r['buy']),self.station_label(r['sell']),f"{r['profit']:,.0f}",f"{r['total_distance']:.1f}")
            iid=self.routes_tree.insert('','end',iid=str(i),values=values)
            if tuple(map(str,values[:4]))==previous_key:selected=iid
        if self.shown_routes:
            iid=selected or '0';self.routes_tree.selection_set(iid);self.select_route()
        else:
            self.variables['trade_buy'].set('No feasible route meets this profit threshold.')
            self.variables['trade_sell'].set('Lower the minimum profit or refresh after visiting more stations.')
            self.variables['trade_profit'].set('')
        data=self.trade_data;when=datetime.fromisoformat(data['read_at']).astimezone().strftime('%H:%M:%S')
        skipped=f" · {data['unpriced_markets']} markets without loaded prices omitted" if data['unpriced_markets'] else ''
        x,y=data['current']['coordinates']
        self.variables['trade_status'].set(f"Updated {when} · Sector ({x}, {y}) · Free cargo {data['cargo_free']:,.1f}/{data['cargo_capacity']} · {data['stations']} stations · {len(self.shown_routes)} routes shown"+skipped)
    def select_route(self,_event=None):
        selected=self.routes_tree.selection()
        if not selected:return
        r=self.shown_routes[int(selected[0])];buy=r['buy'];sell=r['sell']
        self.variables['trade_buy'].set(f"BUY {r['quantity']:,} × {r['good']} at {self.station_label(buy)} — {buy['price']:,.2f} credits each")
        self.variables['trade_sell'].set(f"SELL at {self.station_label(sell)} — {sell['price']:,.2f} credits each")
        self.variables['trade_profit'].set(f"Estimated profit {r['profit']:,.2f} · Buy cost {r['cost']:,.2f} · To buy {r['to_buy']:.1f} pc + trade leg {r['trade_leg']:.1f} pc\nStation positions: buy ({buy['position'][0]}, {buy['position'][1]}) · sell ({sell['position'][0]}, {sell['position'][1]}) · {buy['faction']} → {sell['faction']}")
    def render_equipment(self):
        if not self.latest: return
        self.equipment_tree.delete(*self.equipment_tree.get_children())
        query=self.search.get().casefold()
        for e in self.latest['equipment']:
            if query not in e['name'].casefold(): continue
            self.equipment_tree.insert('', 'end',values=(e['name'],e['quality'],e['quantity'],f"{e['space']:g}" if e['space'] is not None else '?',e['tech']),tags=(e['quality'],))
    def apply_snapshot(self,data):
        self.latest=data; s=data['summary']; v=self.variables
        v['ship'].set(s['ship']); v['status'].set(f"Connected · PID {data['pid']} · live player ship")
        self.status_dot.configure(foreground=ACCENT); self.discovery['value']=100
        readings={'hull':f"{number(s['hull'])} / {number(s['hull_max'])}",
                  'energy':f"{number(s['energy'])} / {number(s['energy_max'])}",
                  'shield':f"{number(s['shields'])} / {number(s['shields_max'])}",
                  'cargo':number(s['cargo_capacity'],0),'credits':number(s['credits'],0),
                  'equipment':f"{s['equipment_used']:g} / {s['equipment_capacity']:g}",
                  'weapons':f"{s['weapon_capacity']:g}",'cooling':f"{s['weapon_cooling']:g}/s",
                  'mining_speed':f"{s['mining_speed_bonus']:+.2%}",'mining_loot':f"{s['mining_loot_bonus']:+.2%}",
                  'geology':f"{s['geology_bonus']:+d}",'metals':f"{s['special_metal_bonus']:+.2%}",
                  'drone_damage':f"{s['drone_damage_bonus']:+.1%}",'drone_hp':f"{s['drone_hp_bonus']:+g}",
                  'drone_speed':f"{s['drone_speed_bonus']:+g}",'drone_auto':'Enabled' if s['drone_auto_targeting'] else 'Off',
                  'drone_parts':'Enabled' if s['drone_parts_recovery'] else 'Off','generation':f"{s['energy_generation']:.1f}/s",
                  'draw':f"{s['passive_energy_draw']:.1f}/s",'heat':f"{s['reactor_heat']:.1f}",
                  'speed':number(s['speed_stat']),'acceleration':number(s['acceleration'])}
        for key,value in readings.items(): v[key].set(value)
        for key,current,maximum in [('hull','hull','hull_max'),('energy','energy','energy_max'),('shield','shields','shields_max')]:
            self.progress_bars[key]['value']=max(0,min(100,s[current]/s[maximum]*100)) if s[maximum]>0 else 0
        v['crew'].set('Crew efficiency   '+ '   ·   '.join(f"{c['role']} {c['efficiency']:.0%}" for c in data['crew']))
        when=datetime.fromisoformat(data['read_at']).astimezone().strftime('%H:%M:%S')
        v['updated'].set(f'Last live snapshot {when}')
        v['pointer'].set(f"PlayerControl.inst [{data['root_address']}] → {data['objects']['PlayerControl']}\nSpaceShip {data['objects']['SpaceShip']} → ShipStats {data['objects']['ShipStats']}")
        equipment_signature=json.dumps(data['equipment'],sort_keys=True)
        if equipment_signature!=self._equipment_signature:
            self._equipment_signature=equipment_signature; self.render_equipment()
        signature=json.dumps(data['modifications'],sort_keys=True)
        if signature!=self._mod_signature:
            self._mod_signature=signature; self.mods_tree.delete(*self.mods_tree.get_children())
            for i,m in enumerate(data['modifications'],1): self.mods_tree.insert('','end',values=(i,m['tier'],m['description']))
        existing=set(self.memory_tree.get_children()); current=set()
        for row in data['memory']:
            iid=row['object']+'.'+row['field']; current.add(iid)
            value=row['value']; value=f'{value:.6g}' if isinstance(value,float) else str(value)
            values=(row['object'],row['field'],row['offset'],row['address'],row['type'],value)
            if iid in existing: self.memory_tree.item(iid,values=values)
            else: self.memory_tree.insert('','end',iid=iid,values=values)
        for iid in existing-current: self.memory_tree.delete(iid)
        self.export_button.configure(state='normal')
    def poll(self):
        if self._closed: return
        for _ in range(100):
            try: event=self.events.get_nowait()
            except queue.Empty: break
            if event[0]=='snapshot': self.apply_snapshot(event[1])
            elif event[0]=='status':
                self.variables['status'].set(event[1]); self.status_dot.configure(foreground='#e9bb73')
                self.discovery['value']=event[2]*100
            elif event[0]=='trades':
                self.trade_data=event[1];self.render_trades()
            elif event[0]=='trade_status':self.variables['trade_status'].set(event[1])
            elif event[0]=='sales':self.selling_page.set_data(event[1])
            elif event[0]=='sale_status':self.selling_page.status.set(event[1])
            elif event[0]=='purchases':self.buying_page.set_data(event[1])
            elif event[0]=='buy_status':self.buying_page.status.set(event[1])
        self._poll_after=self.after(100,self.poll)
    def pause(self):
        if not self.worker: return
        if self.worker.pause_event.is_set():
            self.worker.pause_event.clear(); self.pause_button.configure(text='Pause')
        else:
            self.worker.pause_event.set(); self.pause_button.configure(text='Resume')
            self.worker.cancel_discovery.set()
        self.worker.wake.set()
        if self.trade_worker:
            if self.worker.pause_event.is_set():self.trade_worker.paused.set()
            else:self.trade_worker.paused.clear()
        if self.sale_worker:
            if self.worker.pause_event.is_set():self.sale_worker.paused.set()
            else:self.sale_worker.paused.clear()
        if self.buy_worker:
            if self.worker.pause_event.is_set():self.buy_worker.paused.set()
            else:self.buy_worker.paused.clear()
    def reconnect(self):
        if self.worker:
            self.worker.pause_event.clear(); self.pause_button.configure(text='Pause')
            self.worker.rescan_event.set(); self.worker.wake.set()
            self.worker.cancel_discovery.set()
            self.variables['status'].set('Reconnecting — discovering the active player…')
            if self.trade_worker:self.trade_worker.paused.clear()
            if self.sale_worker:self.sale_worker.paused.clear()
            if self.buy_worker:self.buy_worker.paused.clear()
    def export(self):
        if not self.latest: return
        path=filedialog.asksaveasfilename(parent=self,title='Export ship snapshot',defaultextension='.json',
                                        initialfile='star-valor-ship.json',filetypes=[('JSON snapshot','*.json')])
        if path:
            try: Path(path).write_text(json.dumps(dict(self.latest,trading=self.trade_data),indent=2),encoding='utf-8')
            except OSError as exc: messagebox.showerror('Could not export',str(exc),parent=self)
    def close(self):
        if self._closed:return
        try:window_state.save(self._window_path,window_state.capture_handle(window_state.window_handle(self)))
        except OSError:logging.exception('Could not save window position')
        self._closed=True
        if getattr(self,'_poll_after',None):self.after_cancel(self._poll_after)
        if self.worker: self.worker.shutdown()
        if self.trade_worker:self.trade_worker.shutdown()
        if self.sale_worker:self.sale_worker.shutdown()
        if self.buy_worker:self.buy_worker.shutdown()
        self.destroy()


def setup_logging():
    STATE_DIR.mkdir(exist_ok=True)
    handler=RotatingFileHandler(STATE_DIR/'companion.log',maxBytes=1_000_000,backupCount=2,encoding='utf-8')
    logging.basicConfig(level=logging.INFO,handlers=[handler],format='%(asctime)s %(levelname)s %(message)s')


def main():
    parser=argparse.ArgumentParser(description='Read-only native Star Valor companion')
    parser.add_argument('--probe',action='store_true',help='Read one live snapshot to JSON, then exit')
    parser.add_argument('--fresh',action='store_true',help='Ignore the connection cache and rediscover Mono metadata')
    parser.add_argument('--output',type=Path,help='JSON path for --probe')
    parser.add_argument('--tab',default='Overview',choices=['Overview','Equipment','Ship modifications','Trading','Sell cargo','Buy items','Memory map'],help='Initially selected tab')
    parser.add_argument('--paused',action='store_true',help='Start with live reads paused')
    parser.add_argument('--keep-on-top',action='store_true',help='Keep the window above other windows')
    args=parser.parse_args(); setup_logging()
    if args.probe:
        conn=Connection(lambda text,p:print(f'{p:.0%} {text}',file=sys.stderr),threading.Event())
        try:
            conn.connect(fresh=args.fresh); data=conn.read()
            output=json.dumps(data,indent=2)
            if args.output: args.output.write_text(output,encoding='utf-8')
            else: print(output)
        finally: conn.close()
    else:
        app=Companion(start_paused=args.paused)
        for tab in app.tabs.tabs():
            if app.tabs.tab(tab,'text')==args.tab:app.tabs.select(tab);break
        if args.keep_on_top:app.topmost.set(True);app.attributes('-topmost',True)
        try: app.mainloop()
        finally:
            if app.worker: app.worker.shutdown(); app.worker.join(timeout=2)
            if app.trade_worker:app.trade_worker.shutdown();app.trade_worker.join(timeout=2)
            if app.sale_worker:app.sale_worker.shutdown();app.sale_worker.join(timeout=2)
            if app.buy_worker:app.buy_worker.shutdown();app.buy_worker.join(timeout=2)


if __name__=='__main__': main()
