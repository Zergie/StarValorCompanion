"""Read sector exploration flags and fog counts without revealing hidden locations."""
from datetime import datetime,timezone
import math

from reader import Cancelled,MemoryUnavailable
from trading import TradeReader,FACTIONS


def filter_sectors(rows,query='',state='all'):
    result=[]
    for row in rows:
        if not row['known']:continue
        accept={'all':True,'visited':row['visited'],'incomplete':row['visited'] and not row['fully_explored'],
                'complete':row['fully_explored'],'unvisited':not row['visited']}[state]
        text=f"{row.get('name','')} {row['coordinates']} {row['status']} {row.get('faction','')}".casefold()
        if accept and all(t in text for t in query.casefold().split()):result.append(row)
    return result


def summarize_sectors(sectors):
    total=len(sectors);known=visited=complete=unknown=tiles=cleared=0
    for sector in sectors:
        known+=sector['known'];visited+=sector['visited'];complete+=sector['fully_explored']
        if sector['visited']:
            if sector['explored_percent'] is None:unknown+=1
            else:tiles+=sector['total_tiles'];cleared+=sector['total_tiles']-sector['remaining_tiles']
    return dict(total=total,known=known,visited=visited,fully_explored=complete,unvisited=total-visited,
                uncharted=total-known,unfinished=visited-complete,unknown_fog=unknown,
                visited_tiles=tiles,cleared_tiles=cleared,fog_cleared_percent=cleared/tiles*100 if tiles else 0)


def sector_progress(record,remaining,current_coordinates):
    visited=bool(record['discovered']);known=bool(record['spotted']) or visited
    tiles=(record['fowXtiles']+1)*(record['fowYtiles']+1)
    measurable=visited and record['generated'] and remaining is not None and tiles>0 and 0<=remaining<=tiles
    percent=(1-remaining/tiles)*100 if measurable else None
    complete=measurable and remaining==0
    faction_id=record.get('factionControl')
    faction=FACTIONS[faction_id] if faction_id is not None and 0<=faction_id<len(FACTIONS) else 'Unclaimed' if faction_id==-1 else 'Unknown control'
    status='Fully explored' if complete else 'Visited · fog unknown' if visited and not measurable else 'Visited · incomplete' if visited else 'Spotted · unvisited' if known else 'Uncharted'
    return dict(known=known,visited=visited,fully_explored=bool(complete),status=status,
                explored_percent=percent,total_tiles=tiles,remaining_tiles=remaining,
                coordinates=[record['x'],record['y']],distance=math.dist(current_coordinates,[record['x'],record['y']]),faction_id=faction_id,faction=faction)


class ExplorationReader(TradeReader):
    def read(self):
        c=self.connection;game=self.game_data();m=c.mono;pc,ship=m.player(c.root)
        gd=m.selected(game,['sectors','currentSectorIndex']);objects=m.list(gd['sectors'],limit=20000)
        index=gd['currentSectorIndex']
        if not 0<=index<len(objects):raise MemoryUnavailable('Waiting for the current sector.')
        current=m.selected(objects[index],['x','y']);coordinates=[current['x'],current['y']]
        sectors=[]
        for i,obj in enumerate(objects):
            if c.stop.is_set():raise Cancelled()
            record=m.selected(obj,['spotted','discovered','generated','x','y','name','level','fow','fowXtiles','fowYtiles','factionControl'])
            remaining=m.get(record['fow'],'_size') if record['fow'] else None
            if remaining is not None and not 0<=remaining<=200000:raise MemoryUnavailable('Fog data changed during the read; refresh exploration.')
            row=sector_progress(record,remaining,coordinates)
            if row['known']:
                row.update(index=i,name=m.string(record.get('name',0)),level=record['level'])
            sectors.append(row)
        summary=summarize_sectors(sectors)
        # Uncharted sectors contribute only anonymous counts, not coordinates/names.
        visible=sorted((s for s in sectors if s['known']),key=lambda s:(s['distance'],s['coordinates']))
        if m.player(c.root)!=(pc,ship) or m.get(game,'currentSectorIndex')!=index or m.get(game,'sectors')!=gd['sectors'] or m.list(gd['sectors'],limit=20000)!=objects:
            raise MemoryUnavailable('Your sector or galaxy changed; refresh exploration.')
        return dict(read_at=datetime.now(timezone.utc).isoformat(),pid=c.memory.pid,current=dict(coordinates=coordinates),
                    summary=summary,sectors=visible,source='Live sector flags and fog-of-war counts; totals cover sectors recorded in this save')
