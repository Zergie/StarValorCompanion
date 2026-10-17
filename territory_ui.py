"""Sector ownership map: each coloured cell represents one known sector."""
from collections import Counter
import math
import tkinter as tk
from tkinter import ttk

from trading import FACTIONS

COLORS={-1:'#666d79',0:'#b6c0ca',1:'#629fff',2:'#ba80ed',3:'#f36d77',4:'#7fce91',5:'#f5bd68',6:'#58d6d4',None:'#414953'}


def fit_view(coordinates,width,height):
    if not coordinates:return 0,0,20
    xs,ys=zip(*coordinates)
    center=((min(xs)+max(xs))/2,(min(ys)+max(ys))/2)
    scale=max(.25,min(42,max(1,width-48)/(max(xs)-min(xs)+2),max(1,height-48)/(max(ys)-min(ys)+2)))
    return *center,scale


class TerritoryMap(ttk.Frame):
    def __init__(self,parent,on_select):
        super().__init__(parent)
        self.on_select=on_select;self.rows=[];self.current=None;self.selected=None
        self.cx=0;self.cy=0;self.scale=20;self.auto_fit=True;self.pending=None;self.drag=None
        header=ttk.Frame(self);header.pack(fill='x',pady=(0,4))
        self.caption=tk.StringVar(value='Faction territory · Drag to pan; mouse wheel to zoom')
        ttk.Label(header,textvariable=self.caption,style='Muted.TLabel').pack(side='left')
        for label,action in [('Fit',self.fit),('−',lambda:self.zoom(.8)),('+',lambda:self.zoom(1.25))]:
            ttk.Button(header,text=label,command=action,width=4).pack(side='right',padx=(4,0))
        self.canvas=tk.Canvas(self,bg='#0c121a',highlightthickness=0,height=190)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda _:self.fit() if self.auto_fit else self.schedule())
        self.canvas.bind('<MouseWheel>',lambda e:self.zoom(1.25 if e.delta>0 else .8,e.x,e.y))
        self.canvas.bind('<ButtonPress-1>',self.press);self.canvas.bind('<B1-Motion>',self.move);self.canvas.bind('<ButtonRelease-1>',self.release)
        legend=ttk.Frame(self);legend.pack(fill='x',pady=(4,0))
        self.legend={}
        for i,key in enumerate([0,1,2,3,4,5,6,-1]):
            label=tk.Label(legend,bg='#10171f',fg=COLORS[key],font=('Segoe UI',9),anchor='w')
            label.grid(row=i//4,column=i%4,sticky='w',padx=(0,15));self.legend[key]=label
        self.canvas.pack_forget();legend.pack_forget()
        legend.pack(side='bottom',fill='x',pady=(4,0));self.canvas.pack(fill='both',expand=True)
        self.bind('<Destroy>',self.cleanup)

    def cleanup(self,event):
        if event.widget==self and self.pending is not None:self.after_cancel(self.pending);self.pending=None

    def set_data(self,data):
        self.rows=[r for r in data['sectors'] if r['known']]
        self.caption.set('Faction territory · Drag to pan; mouse wheel to zoom')
        self.current=data['current']['coordinates'];self.selected=None;self.fit()
        counts=Counter(r.get('faction_id') for r in self.rows)
        for key,label in self.legend.items():label.configure(text=f"■ {FACTIONS[key] if key>=0 else 'Unclaimed'} ({counts[key]})")

    def fit(self):
        self.auto_fit=True
        self.cx,self.cy,self.scale=fit_view([r['coordinates'] for r in self.rows],self.canvas.winfo_width(),self.canvas.winfo_height())
        self.schedule()

    def zoom(self,factor,x=None,y=None):
        c=self.canvas;x=c.winfo_width()/2 if x is None else x;y=c.winfo_height()/2 if y is None else y
        wx=self.cx+(x-c.winfo_width()/2)/self.scale;wy=self.cy-(y-c.winfo_height()/2)/self.scale
        self.scale=max(.25,min(160,self.scale*factor));self.auto_fit=False
        self.cx=wx-(x-c.winfo_width()/2)/self.scale;self.cy=wy+(y-c.winfo_height()/2)/self.scale
        self.schedule()

    def press(self,event):self.drag=(event.x,event.y,self.cx,self.cy)

    def move(self,event):
        if not self.drag:return
        x,y,cx,cy=self.drag;self.auto_fit=False
        self.cx=cx-(event.x-x)/self.scale;self.cy=cy+(event.y-y)/self.scale;self.schedule()

    def release(self,event):
        if not self.drag:return
        x,y,_,_=self.drag;self.drag=None
        if abs(event.x-x)+abs(event.y-y)>5:return
        for item in reversed(self.canvas.find_overlapping(event.x-3,event.y-3,event.x+3,event.y+3)):
            for tag in self.canvas.gettags(item):
                if tag.startswith('sector:'):
                    row=self.rows[int(tag.split(':')[1])];self.select(row);self.on_select(row);return

    def select(self,row):
        self.selected=row['coordinates']
        self.caption.set(f"Sector {tuple(row['coordinates'])} · {row.get('faction','Unknown control')} · {row['status']}")
        self.schedule()

    def schedule(self):
        if self.pending is None:self.pending=self.after_idle(self.draw)

    def draw(self):
        self.pending=None;c=self.canvas;c.delete('all');w=c.winfo_width();h=c.winfo_height()
        if not self.rows:
            c.create_text(w/2,h/2,text='Refresh exploration to map known sector territory.',fill='#93a6b8');return
        grid=max(1,math.ceil(50/self.scale))
        minx=self.cx-w/(2*self.scale);miny=self.cy-h/(2*self.scale)
        for gx in range(math.ceil(minx/grid)*grid,math.ceil((self.cx+w/(2*self.scale))/grid)*grid+1,grid):
            x=w/2+(gx-self.cx)*self.scale;c.create_line(x,0,x,h,fill='#1b2836')
        for gy in range(math.ceil(miny/grid)*grid,math.ceil((self.cy+h/(2*self.scale))/grid)*grid+1,grid):
            y=h/2-(gy-self.cy)*self.scale;c.create_line(0,y,w,y,fill='#1b2836')
        size=max(2,min(22,self.scale*.42))
        for i,row in enumerate(self.rows):
            sx,sy=row['coordinates'];x=w/2+(sx-self.cx)*self.scale;y=h/2-(sy-self.cy)*self.scale
            if not -30<x<w+30 or not -30<y<h+30:continue
            tag=f'sector:{i}';color=COLORS.get(row.get('faction_id'),COLORS[None])
            c.create_rectangle(x-size,y-size,x+size,y+size,fill=color,outline='#e5edf5' if row['fully_explored'] else '#10171f',tags=(tag,))
            if row['coordinates']==self.current:
                c.create_oval(x-size-4,y-size-4,x+size+4,y+size+4,outline='white',width=2,tags=(tag,))
                c.create_text(x,y-size-13,text='YOU',fill='white',font=('Segoe UI',8,'bold'),tags=(tag,))
            if row['coordinates']==self.selected:c.create_rectangle(x-size-7,y-size-7,x+size+7,y+size+7,outline='#ffda84',width=2,tags=(tag,))
        c.create_text(8,h-8,anchor='sw',text='One cell = one known sector · White edge = fully explored · Unknown ownership = dark grey',fill='#93a6b8',font=('Segoe UI',8))
