"""Native cargo/stash selection and single-station sale plans."""
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

from selling import Exclusions, inventory_rows, calculate_sales, load_allow_discard, save_allow_discard


class SellingPage(ttk.Frame):
    def __init__(self,parent,refresh,state_path):
        super().__init__(parent,padding=(0,14))
        self.exclusions=Exclusions(state_path);self.data=None;self.plans=[];self.rows={}
        top=ttk.Frame(self);top.pack(fill='x',pady=(0,8))
        ttk.Label(top,text='Sell fleet cargo & resource stash',font=('Segoe UI Semibold',14)).pack(side='left')
        ttk.Button(top,text='Refresh cargo & prices',command=refresh).pack(side='right')
        self.status=tk.StringVar(value='Click Refresh cargo & prices to read your inventory and discovered station markets.')
        ttk.Label(self,textvariable=self.status,style='Muted.TLabel',wraplength=1000).pack(fill='x',pady=(0,10))
        note=ttk.Label(self,text='Planning only: sell or discard in the game yourself. Estimates exclude travel costs. Unknown quotes require review, never discard.',style='Muted.TLabel',wraplength=1000)
        note.pack(side='bottom',fill='x',pady=(10,0))
        self.bind('<Configure>',lambda event:note.configure(wraplength=max(300,event.width-12)))
        panes=ttk.Panedwindow(self,orient='horizontal');panes.pack(fill='both',expand=True)
        left=ttk.Frame(panes,padding=(0,0,12));right=ttk.Frame(panes)
        panes.add(left,weight=2);panes.add(right,weight=3)
        ttk.Label(left,text='Included items',font=('Segoe UI Semibold',12)).pack(anchor='w')
        ttk.Label(left,text='Click a checkbox or press Space to include/exclude.\nExcluded items stay visible, even at zero quantity.',style='Muted.TLabel').pack(anchor='w',pady=(4,8))
        self.inventory=self.make_tree(left,('Use','Item','Active','Fleet','Stash'),(42,160,50,50,55),'Item')
        self.inventory.tag_configure('excluded',foreground='#e9bb73')
        self.inventory.bind('<Button-1>',self.click_item)
        self.inventory.bind('<space>',self.toggle_selected)
        ttk.Button(left,text='Toggle selected item',command=self.toggle_selected).pack(anchor='w',pady=(8,0))
        self.settings_path=self.exclusions.path.with_name('sale-settings.json')
        self.allow_discard=tk.BooleanVar(value=load_allow_discard(self.settings_path))
        ttk.Checkbutton(right,text='Allow discarding items this station does not buy',variable=self.allow_discard,command=self.change_allow_discard).pack(anchor='w',pady=(0,6))
        ttk.Label(right,text='Highest total sale value first; distance breaks ties.\nUnchecked items are protected from sale and discard.',style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        results=ttk.Panedwindow(right,orient='vertical');results.pack(fill='both',expand=True)
        station_frame=ttk.Frame(results);results.add(station_frame,weight=1)
        self.stations=self.make_tree(station_frame,('Station','Sale cr','Discard','pc'),(240,105,65,55),'Station')
        self.stations.configure(height=5)
        self.stations.tag_configure('unknown',foreground='#e9bb73')
        self.stations.bind('<<TreeviewSelect>>',self.select_plan)
        detail_panel=ttk.Frame(results);results.add(detail_panel,weight=1)
        ttk.Label(detail_panel,text='Selected station · item-by-item plan',font=('Segoe UI Semibold',11)).pack(anchor='w',pady=(10,6))
        detail_frame=ttk.Frame(detail_panel);detail_frame.pack(fill='both',expand=True)
        self.detail=tk.Text(detail_frame,height=12,wrap='word',background='#19232e',foreground='#e5edf5',
                            relief='flat',font=('Segoe UI',10),padx=12,pady=10,state='disabled')
        scroll=ttk.Scrollbar(detail_frame,orient='vertical',command=self.detail.yview)
        self.detail.configure(yscrollcommand=scroll.set)
        self.detail.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        self.render_inventory()

    @staticmethod
    def make_tree(parent,columns,widths,stretch):
        frame=ttk.Frame(parent);frame.pack(fill='both',expand=True)
        tree=ttk.Treeview(frame,columns=columns,show='headings',selectmode='browse',height=8)
        for name,width in zip(columns,widths):
            tree.heading(name,text=name);tree.column(name,width=width,minwidth=width if name!=stretch else 110,stretch=name==stretch)
        scroll=ttk.Scrollbar(frame,orient='vertical',command=tree.yview)
        tree.configure(yscrollcommand=scroll.set);tree.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')
        return tree

    def set_data(self,data):
        self.data=data;self.render_inventory();self.recalculate()

    def render_inventory(self):
        selected=self.inventory.selection()
        self.inventory.delete(*self.inventory.get_children())
        rows=inventory_rows(self.data['inventory'] if self.data else [],self.exclusions.items)
        self.rows={r['key']:r for r in rows}
        for r in rows:
            self.inventory.insert('','end',iid=r['key'],values=('☐' if r['excluded'] else '☑',r['name'],f"{r.get('active_loaded',r['loaded']):,}",f"{r.get('fleet_loaded',0):,}",f"{r['stash']:,}"),tags=('excluded',) if r['excluded'] else ())
        if selected and selected[0] in self.rows:self.inventory.selection_set(selected[0])

    def click_item(self,event):
        row=self.inventory.identify_row(event.y)
        if row and self.inventory.identify_column(event.x)=='#1':
            self.inventory.selection_set(row);self.toggle_selected();return 'break'

    def toggle_selected(self,event=None):
        selected=self.inventory.selection()
        if not selected:return 'break'
        key=selected[0]
        try:self.exclusions.toggle(key,self.rows[key]['name'])
        except OSError as exc:
            messagebox.showerror('Could not save item exclusion',str(exc),parent=self);return 'break'
        self.render_inventory();self.recalculate();return 'break'

    def change_allow_discard(self):
        try:save_allow_discard(self.settings_path,self.allow_discard.get())
        except OSError as exc:messagebox.showerror('Could not save discard setting',str(exc),parent=self)
        self.recalculate()

    def recalculate(self):
        self.stations.delete(*self.stations.get_children());self.plans=[]
        if not self.data:return
        self.plans=calculate_sales(self.data,self.exclusions.items,self.allow_discard.get())
        for index,plan in enumerate(self.plans):
            st=plan['station'];x,y=st['coordinates'];label=f"{st['station']} [{st['level']}] ({x}, {y})"
            self.stations.insert('','end',iid=str(index),values=(label,f"{plan['proceeds']:,.0f}"+(' + ?' if plan['unknown'] else ''),f"{plan['discarded']:,}",f"{plan['distance']:.1f}"),tags=('unknown',) if plan['unknown'] else ())
        when=datetime.fromisoformat(self.data['read_at']).astimezone().strftime('%H:%M:%S')
        selected=[r for r in self.data['inventory'] if r['key'] not in self.exclusions.items]
        self.status.set(f"Snapshot {when} · Included: {sum(r.get('active_loaded',r['loaded']) for r in selected):,} active + {sum(r.get('fleet_loaded',0) for r in selected):,} fleet + {sum(r['stash'] for r in selected):,} stash units · {len(self.exclusions.items)} saved exclusions · {len(self.plans)} station plans")
        if self.plans:
            self.stations.selection_set('0');self.select_plan()
        else:
            self.show_detail('No station can sell the selected load under these settings.\n\nInclude items, refresh prices, or allow discarding unbought items. Excluded items will always be kept.')

    def select_plan(self,event=None):
        selection=self.stations.selection()
        if not selection:return
        index=int(selection[0])
        if index>=len(self.plans):return
        plan=self.plans[index];st=plan['station']
        lines=[f"{st['station']} · {st['faction']} · Sector {tuple(st['coordinates'])}",
               f"Sale proceeds: {plan['proceeds']:,.2f} cr · Distance: {plan['distance']:.1f} pc"]
        if plan['unknown']:lines+=['INCOMPLETE QUOTE: unknown items below need review or exclusion. Complete plans rank first.']
        if plan['discarded']:lines+=[f"Discard required: {plan['discarded']:,} units (zero proceeds)"]
        lines+=['']
        for item in sorted(plan['lines'],key=lambda item:(item['status']!='sell',-item['value'] if item['status']=='sell' else 0)):
            origin=f"{item.get('active_loaded',item['loaded']):,} active + {item.get('fleet_loaded',0):,} fleet + {item['stash']:,} stash"
            if item['status']=='sell':
                lines.append(f"SELL {item['quantity']:,} × {item['name']} @ {item['price']:,.2f} = {item['value']:,.2f} cr ({origin})")
            elif item['status']=='reject':lines.append(f"DISCARD {item['quantity']:,} × {item['name']} — station does not buy it ({origin})")
            else:lines.append(f"REVIEW {item['quantity']:,} × {item['name']} — quote unavailable; keep this item ({origin})")
            if item.get('sources'):
                lines.append('  Sources: '+'; '.join(f"{s['name']}: {s['quantity']:,} {s['storage']}" for s in item['sources']))
        lines+=['','All excluded items: KEEP. Shared stash counted once. Bring all selected fleet cargo to the selling station; retrieval and travel costs are excluded.']
        self.show_detail('\n'.join(lines))

    def show_detail(self,text):
        self.detail.configure(state='normal');self.detail.delete('1.0','end');self.detail.insert('1.0',text);self.detail.configure(state='disabled')
