"""Read-only recruitment listings from discovered station academies."""
from datetime import datetime,timezone
import json
import math

from reader import Cancelled,MemoryUnavailable
from selling import QUALITIES
from trading import FACTIONS,TradeReader,station_accessible

ROLES=['Engineer','Pilot','Navigator','Supervisor','Gunner','Instructor','Tactician','Steward','Adviser','Relations Officer']


def rarity_modifier(rarity,modifier):
    if rarity==0:return .7
    return 1+{1:0,2:.2,3:.5,4:1,5:1.6}.get(rarity,0)*modifier


def recruitment_terms(rarity,level,skills,recruitment_skill,skill_modifier):
    skill_count=sum(2**s['rank']*2 for s in skills)
    bonuses=sum(s['bonus_levels'] for s in skills)
    cost=(rarity*200+level*10+skill_count*60+bonuses*100)*1.5*rarity_modifier(rarity,6)*(1-recruitment_skill*skill_modifier)
    requirement=round(max(0,min(50,(2**rarity+skill_count-1+bonuses-4)*rarity_modifier(rarity,1.5)))-recruitment_skill*2)
    return cost,max(0,requirement)


def filter_personnel(rows,query='',sort='distance',descending=False):
    terms=query.casefold().split()
    found=[r for r in rows if all(t in json.dumps(r,ensure_ascii=False).casefold() for t in terms)]
    if sort=='name':return sorted(found,key=lambda r:r['name'].casefold(),reverse=descending)
    return sorted(found,key=lambda r:(r.get(sort) is None,(-r[sort] if descending else r[sort]) if r.get(sort) is not None else 0,r['name'].casefold()))


class PersonnelReader(TradeReader):
    def read(self):
        c=self.connection;game=self.game_data();m=c.mono
        pc,ship=m.player(c.root)
        gd=m.selected(game,['stationList','sectors','currentSectorIndex','character','crew','customNames'])
        sectors=m.list(gd['sectors'],limit=20000);index=gd['currentSectorIndex']
        if not 0<=index<len(sectors):raise MemoryUnavailable('Waiting for the current sector.')
        here=m.selected(sectors[index],['x','y']);current=dict(coordinates=[here['x'],here['y']])
        char=m.selected(gd['character'],['SK','rep']);skills=m.array(char['SK'],'i')
        recruitment=skills[45] if len(skills)>45 else 0
        reps=m.array(m.get(char['rep'],'value'),'i')
        crew_objects=m.list(gd['crew'],limit=20000);crew={m.get(obj,'id'):obj for obj in crew_objects}
        custom_names={}
        if gd.get('customNames'):
            for obj in m.list(m.get(gd['customNames'],'nameList')):
                record=m.selected(obj,['type','id','name'])
                if record.get('type')==0 and record.get('name'):custom_names[record['id']]=m.string(record['name'])
        rows=[];academies=0;unloaded=0;checks=[]
        for obj in m.list(gd['stationList'],limit=20000):
            if c.stop.is_set():raise Cancelled()
            if not m.get(obj,'discovered'):continue
            st=m.selected(obj,['id','ownerID','state','buildingState','factionIndex','sectorIndex','buildingPlanID','modules','dockingControl'])
            if not station_accessible(m,st,reps):continue
            if not 0<=st['sectorIndex']<len(sectors):raise MemoryUnavailable('Station sectors are changing.')
            sec=m.selected(sectors[st['sectorIndex']],['x','y']);coordinates=[sec['x'],sec['y']]
            label=custom_names.get(st['id']) or c.catalog.data['stations'].get(str(st['buildingPlanID']),f"Station #{st['id']}")
            seen=set()
            for module in m.list(st['modules']):
                if m.class_name(m.object_class(module))!='SM_Academy':continue
                academies+=1;ids_pointer=m.get(module,'crewForHireIDs')
                if not ids_pointer:unloaded+=1;continue
                ids=m.list(ids_pointer,'i');checks.append((module,ids_pointer,ids))
                for crew_id in ids:
                    if crew_id in seen:continue
                    seen.add(crew_id);record=crew.get(crew_id)
                    base=dict(crew_id=crew_id,station_id=st['id'],station=label,coordinates=coordinates,
                              faction=FACTIONS[st['factionIndex']] if 0<=st['factionIndex']<len(FACTIONS) else str(st['factionIndex']),
                              distance=math.dist(current['coordinates'],coordinates))
                    if not record:
                        rows.append(dict(base,name=f'Crew #{crew_id}',quality='Unknown',level=None,skills=[],price=None,requirement=None));continue
                    cr=m.selected(record,['aiChar','rarity','skills']);ai=m.selected(cr['aiChar'],['name','nickname','level'])
                    full_name=m.string(ai.get('name',0));nickname=m.string(ai.get('nickname',0))
                    abilities=[]
                    for skill_obj in m.list(cr['skills']):
                        skill=m.selected(skill_obj,['ID','value','skillBonus']);role=skill['ID']
                        bonuses=sum(m.get(b,'level') for b in m.list(skill.get('skillBonus',0)))
                        abilities.append(dict(role=ROLES[role] if 0<=role<len(ROLES) else f'Role {role}',rank=max(0,min(5,int(skill['value']/3000))),raw_value=skill['value'],bonus_levels=bonuses))
                    rarity=cr['rarity'];price,requirement=recruitment_terms(rarity,ai['level'],abilities,recruitment,c.catalog.data['recruitment_skill_mod'])
                    rows.append(dict(base,name=nickname or full_name or f'Crew #{crew_id}',full_name=full_name,
                                     quality=QUALITIES[rarity] if 0<=rarity<len(QUALITIES) else str(rarity),level=ai['level'],
                                     skills=abilities,price=price,requirement=requirement))
        if m.player(c.root)!=(pc,ship) or m.get(game,'currentSectorIndex')!=index:
            raise MemoryUnavailable('Your sector changed; refresh personnel.')
        for module,pointer,ids in checks:
            if m.get(module,'crewForHireIDs')!=pointer or m.list(pointer,'i')!=ids:
                raise MemoryUnavailable('Recruitment listings changed; refresh personnel.')
        return dict(read_at=datetime.now(timezone.utc).isoformat(),pid=c.memory.pid,current=current,
                    personnel=filter_personnel(rows),academies=academies,unloaded_academies=unloaded,
                    source='Live academy recruitment lists; no hiring or generation performed')
