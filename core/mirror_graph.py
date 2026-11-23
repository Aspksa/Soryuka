"""Real, read-only Soryuka memory graph for the Soryuka homepage.

Single SQLite source: no invented nodes, no extra server or network calls.
Only user-confirmed personal memory is included. Disputed notes are visibly tagged.
Activity is derived from existing audit and actual chat messages, not fabricated AI thoughts.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

from .soul_store import SoulStore, StoreError

CATEGORIES = [
    {"id": "memory", "label": "Память", "color": "#55b5f5"},
    {"id": "dialog", "label": "Диалоги", "color": "#6988f4"},
    {"id": "story", "label": "История", "color": "#e8a5ca"},
    {"id": "learning", "label": "Обучение", "color": "#b6a0ff"},
    {"id": "growth", "label": "Развитие", "color": "#54d8bc"},
    {"id": "skill", "label": "Навыки", "color": "#f3be78"},
]
COLORS = {"core": "#f8e5b3", **{x["id"]: x["color"] for x in CATEGORIES}}
ROOT_ID = "core:soryuka"
CAPS = {"memories": 280, "sessions": 55, "messages": 240, "story_events": 80,
        "goals": 90, "episodes": 70, "tasks": 100, "strategies": 65}

def _safe(value, limit=125):
    return " ".join(str(value or "").split())[:limit]

def build_graph(store: SoulStore) -> dict:
    """Snapshot of verified SQLite tables. No synthetic activity or demo data."""
    nodes = [{"id": ROOT_ID, "name": "Сорюка", "category": "core",
              "kind": "character", "size": 17, "status": "canon",
              "detail": "Единое ядро личности; канон остаётся в файлах проекта"}]
    links = []
    ids = {ROOT_ID}
    groups = {}
    def add(id, label, category, kind, size=4, detail="", status="", created_at=""):
        if id in ids:
            return
        ids.add(id)
        nodes.append({"id": id, "name": _safe(label), "category": category,
                      "kind": kind, "size": size, "detail": _safe(detail, 240),
                      "status": status, "created_at": created_at})
    def edge(a,b,relation="contains"):
        if a in ids and b in ids and a != b:
            links.append({"source":a,"target":b,"relation":relation})
    def group(category):
        if category not in groups:
            groups[category] = f"group:{category}"
            label = next(c["label"] for c in CATEGORIES if c["id"]==category)
            add(groups[category],label,category,"cluster",9,"Категория локальных данных")
            edge(ROOT_ID,groups[category],"category")
        return groups[category]

    # Structural category hubs are schema, never invented memories.
    for category in CATEGORIES:
        group(category["id"])
    # Show the real protected personality files as core nodes, just as the reference
    # highlights real SOUL/IDENTITY files. No contents are exposed or fabricated.
    character_dir=Path(__file__).resolve().parents[1]/"character"
    canon=[
        ("soryuka.json","Легенда Сорюки"),
        ("personality.json","Ядро личности"),
        ("living_personality_v2.json","Личность 2.0"),
        ("living_personality_v3.json","Сердце 3.0"),
        ("living_personality_v4.json","Память 4.0"),
        ("living_personality_v5.json","Развитие 5.0"),
        ("SORYUKA_INSTRUCTIONS.md","Правила поведения"),
    ]
    for filename,label in canon:
        if (character_dir/filename).is_file():
            uid="canon:"+filename
            add(uid,label,"core","system",5.5,
                "Защищённый файл: character/"+filename,"real_file")
            edge(ROOT_ID,uid,"canon")

    with store.db() as db:
        for row in db.execute("""
          SELECT m.id,m.body,m.kind,m.created_at,
             COALESCE(p.status,'owner_confirmed') AS provenance
          FROM memories m LEFT JOIN memory_provenance p ON p.memory_id=m.id
          ORDER BY m.created_at DESC,m.id DESC LIMIT ?""",(CAPS["memories"],)):
            uid = "mem:"+row["id"]
            add(uid,row["body"],"memory",row["kind"],5.3,row["body"],
                row["provenance"],row["created_at"])
            edge(group("memory"),uid)

        for row in db.execute("""
          SELECT id,created_at,updated_at FROM sessions
          ORDER BY updated_at DESC LIMIT ?""",(CAPS["sessions"],)):
            uid="session:"+row["id"]
            add(uid,"Беседа "+row["created_at"][:16].replace("T"," "),"dialog",
                "conversation",5.8,"Разговор на этом компьютере","local",row["created_at"])
            edge(group("dialog"),uid)

        for row in db.execute("""
          SELECT id,session_id,role,body,created_at FROM messages
          ORDER BY id DESC LIMIT ?""",(CAPS["messages"],)):
            uid="turn:"+str(row["id"])
            prefix="Вопрос" if row["role"]=="user" else "Ответ"
            add(uid,prefix+" · "+_safe(row["body"],85),"dialog",row["role"],
                2.65,_safe(row["body"],170),"local",row["created_at"])
            edge("session:"+row["session_id"],uid,"message")

        for row in db.execute("""
          SELECT id,body,fiction,created_at FROM story_events
          ORDER BY created_at DESC,id DESC LIMIT ?""",(CAPS["story_events"],)):
            uid="story:"+row["id"]
            category="story"
            add(uid,row["body"],category,"fiction" if row["fiction"] else "event",4.8,
                row["body"],"fiction" if row["fiction"] else "owner_confirmed",row["created_at"])
            edge(group(category),uid)

        for row in db.execute("""
          SELECT id,session_id,digest,source_messages,created_at FROM memory_episodes
          ORDER BY created_at DESC,id DESC LIMIT ?""",(CAPS["episodes"],)):
            uid="episode:"+row["id"]
            add(uid,"Эпизод · "+_safe(row["digest"],85),"dialog","episode",4.4,
                "Сводка из "+str(row["source_messages"])+" сообщений","owner_approved",row["created_at"])
            edge("session:"+row["session_id"],uid,"episode")
            if "session:"+row["session_id"] not in ids:
                edge(group("dialog"),uid)

        skill_ids=set()
        for row in db.execute("""
          SELECT skill,COUNT(*) AS count,AVG(rating) AS average
          FROM feedback GROUP BY skill"""):
            sid="skill:"+row["skill"]
            skill_ids.add(sid)
            add(sid,row["skill"],"skill","skill",5.5,
                f"{row['count']} оценки; средняя {row['average']:.1f}/5",
                "owner_rated")
            edge(group("skill"),sid)

        for row in db.execute("""
          SELECT id,skill,title,status,created_at FROM study_goals
          ORDER BY created_at DESC,id DESC LIMIT ?""",(CAPS["goals"],)):
            uid="goal:"+row["id"]
            add(uid,row["title"],"learning","goal",5.2,row["skill"],row["status"],row["created_at"])
            edge(group("learning"),uid)
            edge("skill:"+row["skill"],uid,"skill")

        for row in db.execute("""
          SELECT id,kind,status,created_at FROM growth_tasks
          ORDER BY created_at DESC,id DESC LIMIT ?""",(CAPS["tasks"],)):
            uid="task:"+row["id"]
            add(uid,row["kind"].replace("_"," "),"growth","task",3.4,
                "Фоновая локальная задача",row["status"],row["created_at"])
            edge(group("growth"),uid)

        for row in db.execute("""
          SELECT id,name,skill,status,created_at FROM academy_strategies
          ORDER BY created_at DESC,id DESC LIMIT ?""",(CAPS["strategies"],)):
            uid="strategy:"+row["id"]
            add(uid,row["name"],"growth","strategy",4.8,row["skill"],row["status"],row["created_at"])
            edge(group("growth"),uid)
            edge("skill:"+row["skill"],uid,"strategy")

        for row in db.execute("""SELECT source_id,target_id,relation FROM memory_links
                                  ORDER BY created_at DESC LIMIT 500"""):
            edge("mem:"+row["source_id"],"mem:"+row["target_id"],row["relation"])
        for row in db.execute("""SELECT cause_id,effect_id FROM causal_links LIMIT 200"""):
            edge("mem:"+row["cause_id"],"mem:"+row["effect_id"],"cause_claim")

        # Read-only cursors mark the present state; subsequent polls return only new events.
        last_audit=db.execute("SELECT COALESCE(MAX(id),0) FROM audit").fetchone()[0]
        last_msg=db.execute("SELECT COALESCE(MAX(id),0) FROM messages").fetchone()[0]

    counts={k:0 for k in COLORS}
    for row in nodes:
        if row["kind"] not in ("cluster","character","system"):
            counts[row["category"]]+=1
    return {"nodes":nodes,"links":links,"categories":CATEGORIES,
            "stats":{"nodes":len(nodes),"links":len(links),"items":sum(counts.values()),
                     "by_category":counts},
            "cursor":{"audit":last_audit,"message":last_msg},
            "source":"local_sqlite","mode":"real_data_not_simulated_cognition"}

EVENT_MAP={
  "memory_approved": ("write","Воспоминание сохранено","memory"),
  "memory_deleted": ("delete","Воспоминание удалено","memory"),
  "memory_link_confirmed": ("link","Связь подтверждена","memory"),
  "memory_link_removed": ("delete","Связь удалена","memory"),
  "memory_merge_approved": ("write","Воспоминания объединены","memory"),
  "memory_provenance_confirmed": ("write","Происхождение проверено","memory"),
  "event_confirmed": ("write","Событие добавлено","story"),
  "event_deleted": ("delete","Событие удалено","story"),
  "goal_approved": ("learn","Учебная цель одобрена","learning"),
  "goal_completed": ("learn","Учебная цель выполнена","learning"),
  "academy_assessment_recorded": ("learn","Учебная проверка","learning"),
  "growth_task_done": ("growth","Фоновая локальная проверка","growth"),
  "growth_task_queued": ("growth","Задание в очереди","growth"),
  "episode_approved": ("write","Эпизод сохранён","dialog"),
  "episode_deleted": ("delete","Эпизод удалён","dialog"),
  "strategy_approved": ("growth","Метод одобрен","growth"),
  "strategy_rolled_back": ("growth","Метод отозван","growth"),
  "feedback_received": ("learn","Оценка навыка","skill"),
  "mirror_chat_started": ("read","Подобраны источники контекста ответа","memory"),
  "mirror_chat_response": ("write","Ответ Сорюки получен","dialog"),
}

def activity(store:SoulStore,audit_after:int=0,message_after:int=0,limit:int=60)->dict:
    try:
        a=int(audit_after); m=int(message_after)
    except (TypeError,ValueError) as exc:
        raise StoreError("Invalid activity cursor") from exc
    if a<0 or m<0 or a>2**53-1 or m>2**53-1:
        raise StoreError("Invalid activity cursor")
    limit=max(1,min(int(limit),80))
    items=[]
    with store.db() as c:
        audits=c.execute("""SELECT id,action,subject,after_json,created_at FROM audit
           WHERE id>? ORDER BY id ASC LIMIT ?""",(a,limit)).fetchall()
        messages=c.execute("""SELECT id,role,session_id,created_at FROM messages
           WHERE id>? ORDER BY id ASC LIMIT ?""",(m,limit)).fetchall()
    for r in audits:
        kind,title,category=EVENT_MAP.get(r["action"],("update","Обновлена локальная запись","growth"))
        targets=[]
        phase=None
        context_kind=None
        subject=str(r["subject"])
        if r["action"] in ("mirror_chat_started","mirror_chat_response"):
            try:
                obj=json.loads(r["after_json"] or "{}")
                memory_ids=obj.get("memory_ids",[])
                if isinstance(memory_ids,list):
                    targets=["mem:"+str(x) for x in memory_ids[:12] if isinstance(x,str)]
                phase=obj.get("phase")
                context_kind=obj.get("context_kind")
            except (ValueError,TypeError):
                pass
        elif r["action"] in ("memory_link_confirmed","memory_link_removed"):
            targets=["mem:"+subject]
            try:
                obj=json.loads(r["after_json"] or "{}")
                other_id=obj.get("other_id")
                if isinstance(other_id,str):
                    targets.append("mem:"+other_id)
            except (ValueError,TypeError):
                pass
        elif r["action"].startswith("memory_"):
            targets=["mem:"+subject]
        elif r["action"].startswith("goal_"):
            targets=["goal:"+subject]
        elif r["action"].startswith("event_"):
            targets=["story:"+subject]
        elif r["action"].startswith("growth_"):
            targets=["task:"+subject]
        elif r["action"].startswith("episode_"):
            targets=["episode:"+subject]
        items.append({"id":"a:"+str(r["id"]),"kind":kind,"title":title,
                      "category":category,"targets":targets,
                      "phase":phase,"context_kind":context_kind,
                      "at":r["created_at"],"source":"sqlite_audit"})
    for r in messages:
        items.append({"id":"m:"+str(r["id"]),
                      "kind":"read" if r["role"]=="user" else "write",
                      "title":"Сообщение пользователя" if r["role"]=="user" else "Ответ Сорюки",
                      "category":"dialog",
                      "targets":["turn:"+str(r["id"]),"session:"+r["session_id"]],
                      "at":r["created_at"],"source":"sqlite_message"})
    items.sort(key=lambda e:(e["at"], e["id"]))
    return {"items":items[-160:],"cursor":{
                "audit":audits[-1]["id"] if audits else a,
                "message":messages[-1]["id"] if messages else m},
            "source":"local_sqlite","simulated":False}
