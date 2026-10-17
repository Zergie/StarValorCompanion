"""Manual recruitment browser; all hiring stays in the game."""
from datetime import datetime
import tkinter as tk
from tkinter import ttk

from personnel import filter_personnel


class PersonnelPage(ttk.Frame):
    COLUMNS=[('name','Crew',170),('quality','Quality',75),('level','Level',55),('skills','Skills / ranks',250),
             ('price','Hire cost cr',100),('requirement','Pilot/Fleet req.',105),('station','Station',200),('distance','Distance pc',90)]

    def __init__(self,parent,refresh):
        super().__init__(parent,padding=(0,14))
        self.data=None;self.shown=[];self.sort_by='distance';self.descending=False
        controls=ttk.Frame(self);controls.pack(fill='x',pady=(0,10))
        ttk.Label(controls,text='Personnel for hire',font=('Segoe UI Semibold',14)).pack(side='left')
        ttk.Button(controls,text='Refresh personnel',command=refresh).pack(side='right')
        search=ttk.Frame(self);search.pack(fill='x',pady=(0,10))
        ttk.Label(search,text='Search',style='Muted.TLabel').pack(side='left',padx=(0,8))
        self.search=tk.StringVar()
        ttk.Entry(search,textvariable=self.search).pack(side='left',fill='x',expand=True)
        ttk.Button(search,text='Clear',command=lambda:self.search.set('')).pack(side='right',padx=(8,0))
        self.search.trace_add('write',lambda *_:self.render())
        self.status=tk.StringVar(value='Click Refresh personnel to read available academy recruits.')
        status=ttk.Label(self,textvariable=self.status,style='Muted.TLabel',wraplength=1000);status.pack(fill='x',pady=(0,10))
        note=ttk.Label(self,text='Read only: hire in the game. Discovered, non-hostile NPC academies only. Ungenerated recruits cannot be previewed. Requirement is minimum Space Pilot OR Fleet Commander; berth, credit and docking restrictions still apply.',style='Muted.TLabel',wraplength=1000)
        note.pack(side='bottom',fill='x',pady=(8,0))
        self.details=tk.StringVar(value='Search by crew name, role, quality, station or faction. Click a numeric column to sort.')
        detail=ttk.Label(self,textvariable=self.details,style='Muted.TLabel',wraplength=1000);detail.pack(side='bottom',fill='x',pady=(8,0))
        frame=ttk.Frame(self);frame.pack(fill='both',expand=True)
        frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
        self.tree=ttk.Treeview(frame,columns=[c[0] for c in self.COLUMNS],show='headings',selectmode='browse')
        for key,title,width in self.COLUMNS:
            self.tree.heading(key,text=title,command=lambda k=key:self.sort(k))
            self.tree.column(key,width=width,minwidth=width,stretch=key in ('name','station'),anchor='e' if key in ('price','requirement','distance','level') else 'w')
        vertical=ttk.Scrollbar(frame,orient='vertical',command=self.tree.yview)
        horizontal=ttk.Scrollbar(frame,orient='horizontal',command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        self.tree.grid(row=0,column=0,sticky='nsew');vertical.grid(row=0,column=1,sticky='ns');horizontal.grid(row=1,column=0,sticky='ew')
        self.tree.bind('<<TreeviewSelect>>',self.select)
        self.bind('<Configure>',lambda e:[w.configure(wraplength=max(300,e.width-12)) for w in (status,note,detail)])

    def set_data(self,data):self.data=data;self.render()

    def sort(self,key):
        if key not in ('name','level','price','requirement','distance'):return
        self.descending=not self.descending if self.sort_by==key else False
        self.sort_by=key;self.render()

    def render(self):
        if not self.data:return
        self.tree.delete(*self.tree.get_children())
        self.shown=filter_personnel(self.data['personnel'],self.search.get(),self.sort_by,self.descending)
        for index,r in enumerate(self.shown):
            abilities=' · '.join(f"{s['role']} {s['rank']}" for s in r['skills']) or 'Unknown'
            self.tree.insert('','end',iid=str(index),values=(r['name'],r['quality'],r['level'] if r['level'] is not None else 'Unknown',abilities,
                f"{r['price']:,.2f}" if r['price'] is not None else 'Unknown',r['requirement'] if r['requirement'] is not None else 'Unknown',
                f"{r['station']} ({r['coordinates'][0]}, {r['coordinates'][1]})",f"{r['distance']:.1f}"))
        when=datetime.fromisoformat(self.data['read_at']).astimezone().strftime('%H:%M:%S')
        self.status.set(f"Snapshot {when} · {len(self.shown)} of {len(self.data['personnel'])} recruits · {self.data['academies']} academies · {self.data['unloaded_academies']} ungenerated rosters")
        self.details.set('Select a recruit for details. Rank 0 = apprentice; rank 5 = legendary.' if self.shown else 'No matching personnel. Change the search or refresh after visiting an academy.')
        for key,title,_ in self.COLUMNS:
            self.tree.heading(key,text=title+(' ▼' if self.descending else ' ▲') if key==self.sort_by else title)

    def select(self,event=None):
        selection=self.tree.selection()
        if not selection:return
        index=int(selection[0])
        if index>=len(self.shown):return
        r=self.shown[index]
        abilities='; '.join(f"{s['role']}: rank {s['rank']} ({s['raw_value']} skill points, bonus levels {s['bonus_levels']})" for s in r['skills']) or 'Skills unavailable'
        self.details.set(f"{r['name']} · {r.get('full_name',r['name'])} · {r['quality']} · {r['station']} · {r['faction']} · {r['distance']:.1f} pc\n{abilities}")
