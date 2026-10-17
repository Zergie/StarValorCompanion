"""Searchable, manually refreshed station purchase listings."""
from datetime import datetime
import tkinter as tk
from tkinter import ttk

from buying import filter_offers


class BuyingPage(ttk.Frame):
    QUALITY_COLORS={'Grey':'#93a6b8','White':'#e5edf5','Green':'#79d69a',
                    'Blue':'#76b8ff','Purple':'#c79dff','Orange':'#ffbd72'}
    COLUMNS=[('name','Item',230),('category','Type',85),('quality','Quality',80),
             ('quantity','Qty',75),('price','Unit price cr',110),('station','Station',250),('distance','Distance pc',100)]

    def __init__(self,parent,refresh):
        super().__init__(parent,padding=(0,14))
        self.data=None;self.shown=[];self.sort_by='distance';self.descending=False
        top=ttk.Frame(self);top.pack(fill='x',pady=(0,12))
        ttk.Label(top,text='Items to buy',font=('Segoe UI Semibold',14)).pack(side='left')
        ttk.Button(top,text='Refresh items',command=refresh).pack(side='right')
        search=ttk.Frame(self);search.pack(fill='x',pady=(0,10))
        ttk.Label(search,text='Search').pack(side='left',padx=(0,10))
        self.search=tk.StringVar()
        self.entry=ttk.Entry(search,textvariable=self.search);self.entry.pack(side='left',fill='x',expand=True)
        ttk.Button(search,text='Clear',command=lambda:self.search.set('')).pack(side='right',padx=(8,0))
        self.entry.bind('<Escape>',lambda _:self.search.set(''))
        self.search.trace_add('write',lambda *_:self.render())
        self.status=tk.StringVar(value='Click Refresh items to read available stock and prices.')
        self.status_label=ttk.Label(self,textvariable=self.status,style='Muted.TLabel',wraplength=1000)
        self.status_label.pack(fill='x',pady=(0,10))
        note=ttk.Label(self,text='Quantity is station stock, not what fits your ship. Prices are per unit. Distance is straight-line sector distance. Only discovered, non-hostile NPC markets with loaded listings are included; refresh is manual.',style='Muted.TLabel',wraplength=1000)
        note.pack(side='bottom',fill='x',pady=(8,0))
        self.selection=tk.StringVar(value='Search by item, quality, type, station or faction. Click a column heading to sort.')
        self.selection_label=ttk.Label(self,textvariable=self.selection,style='Muted.TLabel',wraplength=1000)
        self.selection_label.pack(side='bottom',fill='x',pady=(10,0))
        frame=ttk.Frame(self);frame.pack(fill='both',expand=True)
        frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
        self.tree=ttk.Treeview(frame,columns=[c[0] for c in self.COLUMNS],show='headings',selectmode='browse')
        for quality,color in self.QUALITY_COLORS.items():
            self.tree.tag_configure(quality,foreground=color)
        for key,title,width in self.COLUMNS:
            self.tree.heading(key,text=title,command=lambda k=key:self.sort(k))
            self.tree.column(key,width=width,minwidth=110 if key in ('name','station') else width,stretch=key in ('name','station'),anchor='e' if key in ('quantity','price','distance') else 'w')
        vertical=ttk.Scrollbar(frame,orient='vertical',command=self.tree.yview)
        horizontal=ttk.Scrollbar(frame,orient='horizontal',command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        self.tree.grid(row=0,column=0,sticky='nsew');vertical.grid(row=0,column=1,sticky='ns');horizontal.grid(row=1,column=0,sticky='ew')
        self.tree.bind('<<TreeviewSelect>>',self.select_offer)
        self.bind('<Configure>',lambda e:[w.configure(wraplength=max(300,e.width-12)) for w in (note,self.selection_label,self.status_label)])
        self.update_headings()

    def set_data(self,data):
        self.data=data;self.render()

    def sort(self,key):
        self.descending=not self.descending if self.sort_by==key else False
        self.sort_by=key;self.render()

    def update_headings(self):
        for key,title,_ in self.COLUMNS:
            self.tree.heading(key,text=title+(' ▼' if self.descending else ' ▲') if key==self.sort_by else title)

    def render(self):
        if not self.data:return
        self.tree.delete(*self.tree.get_children())
        self.shown=filter_offers(self.data['purchase_offers'],self.search.get(),self.sort_by,self.descending)
        for index,row in enumerate(self.shown):
            self.tree.insert('','end',iid=str(index),values=(row['name'],row['category'],row['quality'],
                'Unlimited' if row['quantity'] is None else f"{row['quantity']:,}",
                'Unknown' if row['price'] is None else f"{row['price']:,.2f}",
                f"{row['station']} [{row['level']}] ({row['coordinates'][0]}, {row['coordinates'][1]})",f"{row['distance']:.1f}"),tags=(row['quality'],))
        when=datetime.fromisoformat(self.data['read_at']).astimezone().strftime('%H:%M:%S')
        unknown=sum(r['price'] is None for r in self.shown)
        self.status.set(f"Snapshot {when} · {len(self.shown):,} of {len(self.data['purchase_offers']):,} listings · {self.data['stations']} stations · {unknown} unknown prices")
        self.selection.set('Select a listing for station details. Reputation and docking requirements still apply.' if self.shown else 'No matching items. Try another search or refresh the station listings.')
        self.update_headings()

    def select_offer(self,event=None):
        selected=self.tree.selection()
        if not selected:return
        index=int(selected[0])
        if index>=len(self.shown):return
        row=self.shown[index];x,y=row['coordinates']
        price='Quote unavailable' if row['price'] is None else f"{row['price']:,.2f} cr each"
        self.selection.set(f"{row['name']} · {row['quality']} · {price} · {row['station']} · {row['faction']} · Sector ({x}, {y}) · {row['distance']:.1f} pc from your snapshot position. Reputation and docking requirements still apply.")
