"""Exploration progress bars and known-sector checklist."""
from datetime import datetime
import tkinter as tk
from tkinter import ttk
from exploration import filter_sectors
from territory_ui import TerritoryMap


class ExplorationPage(ttk.Frame):
    def __init__(self,parent,refresh):
        super().__init__(parent,padding=(0,14))
        self.data=None;self.bars={};self.counts={};self.shown=[]
        style=ttk.Style(self)
        style.configure('Exploration.TCombobox',fieldbackground='#19232e',foreground='#e5edf5',arrowcolor='#93a6b8')
        style.map('Exploration.TCombobox',fieldbackground=[('readonly','#19232e')],foreground=[('readonly','#e5edf5')],selectbackground=[('readonly','#30505a')],selectforeground=[('readonly','#e5edf5')])
        top=ttk.Frame(self);top.pack(fill='x',pady=(0,8))
        ttk.Label(top,text='Exploration progress',font=('Segoe UI Semibold',14)).pack(side='left')
        ttk.Button(top,text='Refresh exploration',command=refresh).pack(side='right')
        self.status=tk.StringVar(value='Refresh to read exploration progress from your current save.')
        status=ttk.Label(self,textvariable=self.status,style='Muted.TLabel',wraplength=1000);status.pack(fill='x',pady=(0,10))
        cards=ttk.Frame(self);cards.pack(fill='x')
        for col in range(3):cards.columnconfigure(col,weight=1,uniform='exploration')
        for i,(key,label) in enumerate([('visited','Visited sectors'),('fully_explored','Fully explored sectors'),
                ('fog','Fog cleared')]):
            card=ttk.Frame(cards,style='Panel.TFrame',padding=(12,5));card.grid(row=0,column=i,sticky='ew',padx=(0,8) if i<2 else 0,pady=(0,5))
            row=ttk.Frame(card,style='Panel.TFrame');row.pack(fill='x')
            ttk.Label(row,text=label,style='Panel.TLabel').pack(side='left')
            value=tk.StringVar(value='—');self.counts[key]=value
            ttk.Label(row,textvariable=value,style='Panel.TLabel').pack(side='right')
            bar=ttk.Progressbar(card,maximum=100);bar.pack(fill='x',pady=(4,0));self.bars[key]=bar
        filters=ttk.Frame(self);filters.pack(fill='x',pady=(3,8))
        ttk.Label(filters,text='Sectors').pack(side='left',padx=(0,8))
        self.filter=tk.StringVar(value='All known')
        box=ttk.Combobox(filters,textvariable=self.filter,values=['All known','Visited','Not complete','Fully explored','Unvisited'],state='readonly',width=18,style='Exploration.TCombobox')
        box.pack(side='left');box.bind('<<ComboboxSelected>>',lambda _:self.render_sectors())
        ttk.Label(filters,text='Search',style='Muted.TLabel').pack(side='left',padx=8)
        self.search=tk.StringVar();self.search.trace_add('write',lambda *_:self.render_sectors())
        ttk.Entry(filters,textvariable=self.search).pack(side='left',fill='x',expand=True)
        note=ttk.Label(self,text='Totals: current save. Visited = discovered. Complete = visited/generated, no fog. Missing fog stays unknown. Uncharted locations hidden. Manual refresh.',style='Muted.TLabel',wraplength=1000)
        note.pack(side='bottom',fill='x',pady=(8,0))
        panes=ttk.Panedwindow(self,orient='vertical');panes.pack(fill='both',expand=True)
        self.territory=TerritoryMap(panes,self.select_map_sector);panes.add(self.territory,weight=3)
        frame=ttk.Frame(panes);panes.add(frame,weight=1)
        self.tree=ttk.Treeview(frame,columns=['Sector','Name','Faction','Status','Explored','Distance pc'],show='headings',height=3)
        for key,width in [('Sector',95),('Name',120),('Faction',125),('Status',180),('Explored',100),('Distance pc',90)]:
            self.tree.heading(key,text=key);self.tree.column(key,width=width,minwidth=width,stretch=key in ('Name','Status'))
        scroll=ttk.Scrollbar(frame,orient='vertical',command=self.tree.yview);self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        self.tree.bind('<<TreeviewSelect>>',self.select_table_sector)
        self.bind('<Configure>',lambda e:[w.configure(wraplength=max(300,e.width-12)) for w in (status,note)])

    def set_data(self,data):
        self.data=data;s=data['summary'];total=s['total']
        for key in ('visited','fully_explored'):
            percent=s[key]/total*100 if total else 0
            self.counts[key].set(f"{s[key]:,}/{total:,} · {percent:.1f}%");self.bars[key]['value']=percent
        self.counts['fog'].set(f"{s['fog_cleared_percent']:.1f}%");self.bars['fog']['value']=s['fog_cleared_percent']
        when=datetime.fromisoformat(data['read_at']).astimezone().strftime('%H:%M:%S')
        self.status.set(f"Snapshot {when} · {s['known']} known · {s['unvisited']} unvisited · {s['unfinished']} unfinished visits · {s['uncharted']} uncharted · {s['unknown_fog']} unknown fog")
        self.territory.set_data(data)
        self.render_sectors()

    def render_sectors(self):
        if not self.data:return
        self.tree.delete(*self.tree.get_children());self.shown=[]
        mode={'All known':'all','Visited':'visited','Not complete':'incomplete','Fully explored':'complete','Unvisited':'unvisited'}[self.filter.get()]
        for row in filter_sectors(self.data['sectors'],self.search.get(),mode):
            self.shown.append(row)
            self.tree.insert('','end',iid=str(len(self.shown)-1),values=(str(tuple(row['coordinates'])),row.get('name','') or '—',row.get('faction','Unknown control'),row['status'],
                f"{row['explored_percent']:.1f}%" if row['explored_percent'] is not None else 'Unknown',f"{row['distance']:.1f}"))

    def select_table_sector(self,event=None):
        selection=self.tree.selection()
        if selection and int(selection[0])<len(self.shown):self.territory.select(self.shown[int(selection[0])])

    def select_map_sector(self,row):
        if row not in self.shown:
            self.filter.set('All known');self.search.set('');self.render_sectors()
        if row in self.shown:
            key=str(self.shown.index(row));self.tree.selection_set(key);self.tree.see(key)
