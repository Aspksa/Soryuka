"""Soryuka 5.0: durable, opt-in, local-only supervised development cycle.

The worker reuses the existing Python API process and ONE SQLite database.
No agents, external services, LLM or Cloud.ru inference in background tasks.
It produces reviewable proposals; it cannot rewrite canon, memories or model weights.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import math
import threading
import time
from uuid import uuid4

from .soul_store import SoulStore, StoreError, SKILLS, check_id, clean, new_id, now
from .cognitive_v5 import CognitiveV5

KINDS={"memory_review","learning_exercises","growth_recommendations"}
DDL="""
CREATE TABLE IF NOT EXISTS growth_tasks (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN('memory_review','learning_exercises','growth_recommendations')),
 status TEXT NOT NULL CHECK(status IN('queued','running','done','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0,
 scheduled_key TEXT,
 not_before TEXT NOT NULL, lease_until TEXT,
 result_json TEXT, last_error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_growth_tasks_status ON growth_tasks(status,not_before);
CREATE UNIQUE INDEX IF NOT EXISTS ux_growth_schedule ON growth_tasks(kind,scheduled_key)
 WHERE scheduled_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS growth_review (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES growth_tasks(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, candidate_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN('pending','dismissed','accepted')),
 created_at TEXT NOT NULL, decided_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_growth_review_state ON growth_review(status,created_at);
CREATE TABLE IF NOT EXISTS skill_transfers (
 id TEXT PRIMARY KEY,
 from_skill TEXT NOT NULL, to_skill TEXT NOT NULL,
 strategy_id TEXT NOT NULL REFERENCES academy_strategies(id) ON DELETE CASCADE,
 rationale TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN('pending','approved','rejected','revoked')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 CHECK(from_skill <> to_skill)
);
"""


class GrowthV5:
    def __init__(self,store:SoulStore,cognition:CognitiveV5|None=None):
        self.store=store
        self.cognition=cognition or CognitiveV5(store)
        self.cap=self.cognition.cap
        # Prevent concurrent manual/API and background execution on this worker.
        self._run_lock=threading.Lock()
        with store.db() as c:
            c.executescript(DDL)

    def enqueue(self,kind:str,confirmed:bool,scheduled_key:str|None=None)->dict:
        if confirmed is not True:
            raise StoreError("Owner approval required to add background task")
        if kind not in KINDS:
            raise StoreError("Task kind unsupported")
        if scheduled_key is not None and (not isinstance(scheduled_key,str) or len(scheduled_key)>50):
            raise StoreError("Invalid schedule marker")
        with self.store.db() as c:
            c.execute("BEGIN IMMEDIATE")
            pending=c.execute("SELECT COUNT(*) FROM growth_tasks WHERE status IN ('queued','running')").fetchone()[0]
            if pending>=100:
                raise StoreError("Queue is full")
            row={"id":new_id(),"kind":kind,"status":"queued",
                 "scheduled_key":scheduled_key,"not_before":now(),"created_at":now(),"updated_at":now()}
            try:
                c.execute("""INSERT INTO growth_tasks
                    (id,kind,status,scheduled_key,not_before,created_at,updated_at)
                    VALUES (:id,:kind,:status,:scheduled_key,:not_before,:created_at,:updated_at)""",row)
            except Exception as exc:
                if scheduled_key is not None:
                    existing=c.execute("SELECT * FROM growth_tasks WHERE kind=? AND scheduled_key=?",
                                       (kind,scheduled_key)).fetchone()
                    if existing is not None:
                        return {"id":existing["id"],"kind":kind,"status":existing["status"],"deduplicated":True}
                raise
            self.store._audit(c,"growth_task_queued",row["id"],None,json.dumps({"kind":kind}))
        return row

    def tasks(self,limit:int=40)->list[dict]:
        with self.store.db() as c:
            rows=c.execute("""SELECT id,kind,status,attempts,not_before,result_json,last_error,
                created_at,updated_at FROM growth_tasks
                ORDER BY created_at DESC,id DESC LIMIT ?""",
                (max(1,min(int(limit),100)),)).fetchall()
        return [dict(r) for r in rows]

    def recover(self)->int:
        """Resume abandoned tasks after process crash or lease timeout."""
        stamp=now()
        with self.store.db() as c:
            result=c.execute("""UPDATE growth_tasks
                SET status='queued',lease_until=NULL,updated_at=?
                WHERE status='running' AND (lease_until IS NULL OR lease_until<?)""",
                (stamp,stamp))
        return result.rowcount

    def claim(self)->dict|None:
        if not self.cognition.permission("background_local"):
            return None
        with self.store.db() as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("""SELECT id,kind,attempts FROM growth_tasks WHERE status='queued'
                AND not_before<=? AND attempts<3 ORDER BY not_before,id LIMIT 1""",
                (now(),)).fetchone()
            if row is None:
                return None
            lease=(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat(timespec="seconds")
            c.execute("""UPDATE growth_tasks SET status='running',attempts=attempts+1,
                lease_until=?,updated_at=? WHERE id=?""",(lease,now(),row["id"]))
            return dict(row)

    def _review_memory(self)->dict:
        candidate_ids=[r["id"] for r in self.cap.confirmed_memories(18)]
        seen=set();out=[]
        for mid in candidate_ids[:12]:
            for issue in self.cap.review_candidates(mid,4):
                a,b=sorted((mid,issue["memory_id"]))
                if a==b or (a,b) in seen:
                    continue
                seen.add((a,b))
                out.append({"kind":issue["kind"],"first_id":a,"second_id":b,
                            "similarity":issue["similarity"],
                            "owner_review_required":True})
                if len(out)>=12:
                    break
            if len(out)>=12:
                break
        return {"candidates":out,"never_auto_merge":True}

    def _exercises(self)->dict:
        with self.store.db() as c:
            rows=c.execute("""SELECT a.id,a.goal_id,a.skill,a.question,g.status
                FROM academy_assessments a JOIN study_goals g ON g.id=a.goal_id
                WHERE a.passed=0 AND g.status IN ('approved','completed')
                ORDER BY a.created_at DESC,a.id DESC LIMIT 12""").fetchall()
        return {"exercises":[{"source_assessment_id":r["id"],"goal_id":r["goal_id"],
                   "skill":r["skill"],"exercise_available_in_academy":True,
                   "requires_manual_reference_check":True} for r in rows],
                "auto_generated_new_facts":False}

    def _recommendations(self)->dict:
        council=[{**x,"kind":"goal_candidate"} for x in self.cap.development_council()]
        # Skill Forge is proposal-only: zero invented benchmark scores.
        with self.store.db() as c:
            problems=c.execute("""SELECT a.skill,COUNT(*) AS mistakes,
                COUNT(DISTINCT a.goal_id) AS goals FROM academy_assessments a
                WHERE a.passed=0 GROUP BY a.skill HAVING COUNT(*)>=2 LIMIT 6""").fetchall()
        methods=[]
        for p in problems:
            methods.append({"kind":"strategy_candidate","skill":p["skill"],
                "title":"Пошаговая проверка перед ответом",
                "reason":f"{p['mistakes']} несовпадений с эталоном на {p['goals']} целях",
                "trial_plan":"Сравнить не менее 5 контрольных ответов с исходным методом",
                "requires_owner_supplied_benchmark":True,"no_automatic_application":True})
        return {"suggestions":(council+methods)[:12],"requires_owner_approval":True}

    def _execute(self,kind:str)->dict:
        if kind=="memory_review":return self._review_memory()
        if kind=="learning_exercises":return self._exercises()
        if kind=="growth_recommendations":return self._recommendations()
        raise StoreError("Unknown task kind")

    def run_once(self,max_tasks:int=2,max_seconds:float=8)->dict:
        if not self._run_lock.acquire(blocking=False):
            return {"processed":0,"status":"already_running","network_calls":0}
        try:
            return self._run_once_locked(max_tasks,max_seconds)
        finally:
            self._run_lock.release()

    def _run_once_locked(self,max_tasks:int=2,max_seconds:float=8)->dict:
        """Bounded work; safe to call from tests and loopback API. No LLM calls."""
        if not self.cognition.permission("background_local"):
            return {"processed":0,"status":"disabled","network_calls":0}
        max_tasks=max(1,min(int(max_tasks),2))
        max_seconds=max(1,min(float(max_seconds),8))
        started=time.monotonic()
        processed=0
        for _ in range(max_tasks):
            if time.monotonic()-started>=max_seconds:
                break
            task=self.claim()
            if task is None:break
            try:
                output=self._execute(task["kind"])
                # Review records carry only source IDs and results, not duplicated private text.
                entries=output.get("candidates",output.get("exercises",output.get("suggestions",[])))
                with self.store.db() as c:
                    c.execute("BEGIN IMMEDIATE")
                    c.execute("""UPDATE growth_tasks SET status='done',lease_until=NULL,result_json=?,
                         last_error=NULL,updated_at=? WHERE id=?""",
                         (json.dumps({"count":len(entries),"requires_approval":True}),now(),task["id"]))
                    for item in entries[:12]:
                        c.execute("""INSERT INTO growth_review
                          (id,task_id,kind,candidate_json,status,created_at) VALUES(?,?,?,?,?,?)""",
                          (new_id(),task["id"],task["kind"],json.dumps(item,ensure_ascii=False),
                           "pending",now()))
                    self.store._audit(c,"growth_task_done",task["id"],None,
                                     json.dumps({"kind":task["kind"],"count":len(entries)}))
            except Exception:
                with self.store.db() as c:
                    c.execute("BEGIN IMMEDIATE")
                    attempt=c.execute("SELECT attempts FROM growth_tasks WHERE id=?",(task["id"],)).fetchone()
                    n=attempt["attempts"] if attempt else 3
                    new_status="failed" if n>=3 else "queued"
                    delay=0 if n>=3 else 30*(2**(n-1))
                    next_run=(datetime.now(timezone.utc)+timedelta(seconds=delay)).isoformat(timespec="seconds")
                    c.execute("""UPDATE growth_tasks SET status=?,lease_until=NULL,
                         not_before=?,last_error='local_task_failed',updated_at=? WHERE id=?""",
                         (new_status,next_run,now(),task["id"]))
            processed+=1
        return {"processed":processed,"status":"enabled","network_calls":0}

    def schedule_cycle(self)->dict:
        """Only when opt-in background is enabled; at most 3 queue items per 15 min."""
        if not self.cognition.permission("background_local"):
            return {"enqueued":0,"status":"disabled"}
        epoch=int(time.time()//900)
        created=0
        kinds=["memory_review","learning_exercises","growth_recommendations"]
        for kind in kinds:
            result=self.enqueue(kind,True,scheduled_key=str(epoch))
            created+=int(not result.get("deduplicated",False))
        return {"enqueued":created,"cycle":epoch}

    def reviews(self,limit:int=60)->list[dict]:
        with self.store.db() as c:
            rows=c.execute("""SELECT id,task_id,kind,candidate_json,status,created_at,decided_at
                 FROM growth_review ORDER BY created_at DESC,id DESC LIMIT ?""",
                 (max(1,min(int(limit),100)),)).fetchall()
        return [{**dict(r),"candidate":json.loads(r["candidate_json"])} for r in rows]

    def review_action(self,review_id:str,action:str)->dict:
        if action not in ("dismiss","accept"):
            raise StoreError("Invalid review action")
        rid=check_id(review_id)
        with self.store.db() as c:
            c.execute("BEGIN IMMEDIATE")
            record=c.execute("SELECT id,status,kind FROM growth_review WHERE id=?",(rid,)).fetchone()
            if record is None or record["status"]!="pending":
                raise StoreError("Review must be pending")
            # Accept records agreement to investigate; it NEVER merges memory or applies a strategy.
            status="accepted" if action=="accept" else "dismissed"
            c.execute("UPDATE growth_review SET status=?,decided_at=? WHERE id=?",
                      (status,now(),rid))
            self.store._audit(c,"growth_review_"+status,rid,None,json.dumps({"kind":record["kind"]}))
        return {"id":rid,"status":status,"applied_changes":False}

    def plan(self)->list[dict]:
        """Planning is read-only and uses only measured skill/map data."""
        p=self.cap.skill_map()
        reviews=self.reviews(20)
        items=[{"kind":"learning_exercises","reason":"Reviewed goal with measured mistakes",
                "status":"suggested_not_executed"}
               for s in p if s["reference_checks"]["tests"]>=2
               and s["reference_checks"]["passed"]<s["reference_checks"]["tests"]][:4]
        if self.cap.confirmed_memories(2):
            items.append({"kind":"memory_review","reason":"Review approved memory for similarities",
                          "status":"suggested_not_executed"})
        if any(x["status"]=="pending" for x in reviews):
            items.append({"kind":"review_pending","reason":"Owner decisions are awaiting review",
                          "status":"manual_action_required"})
        return items[:6]

    def compare_strategy(self,strategy_id:str)->dict:
        sid=check_id(strategy_id)
        matching=[r for r in self.cap.strategies(100) if r["id"]==sid]
        if not matching:
            raise StoreError("Strategy not found")
        r=matching[0]
        delta=round(r["trial"]-r["baseline"],2)
        return {"id":sid,"skill":r["skill"],"delta_percentage_points":delta,
                "recommendation":"review_for_approval" if delta>0 else "do_not_promote",
                "method":"owner_supplied_comparison","validated_experiment":False}

    def guard_strategy_change(self,strategy_id:str,action:str)->dict:
        if action=="approve":
            result=self.compare_strategy(strategy_id)
            if result["delta_percentage_points"]<=0:
                raise StoreError("Strategy must outperform baseline before approval")
        # Existing V4 approval/rollback flow and audits are preserved.
        return self.cap.strategy_action(strategy_id,action)

    def propose_transfer(self,from_skill:str,to_skill:str,strategy_id:str,rationale:str)->dict:
        if from_skill not in SKILLS or to_skill not in SKILLS or from_skill==to_skill:
            raise StoreError("Choose two distinct known skills")
        strategy=self.compare_strategy(strategy_id)
        existing=[s for s in self.cap.strategies(100) if s["id"]==strategy_id][0]
        if existing["skill"]!=from_skill or existing["status"]!="approved":
            raise StoreError("Only an owner-approved source skill strategy is transferable")
        record={"id":new_id(),"from_skill":from_skill,"to_skill":to_skill,
                "strategy_id":strategy_id,"rationale":clean(rationale,500),
                "status":"pending","created_at":now(),"updated_at":now()}
        with self.store.db() as c:
            c.execute("""INSERT INTO skill_transfers
                (id,from_skill,to_skill,strategy_id,rationale,status,created_at,updated_at)
                VALUES (:id,:from_skill,:to_skill,:strategy_id,:rationale,:status,:created_at,:updated_at)""",record)
        return record

    def transfer_action(self,transfer_id:str,action:str)->dict:
        if action not in ("approve","reject","revoke"):
            raise StoreError("Unknown transfer action")
        tid=check_id(transfer_id)
        with self.store.db() as c:
            c.execute("BEGIN IMMEDIATE")
            r=c.execute("SELECT status FROM skill_transfers WHERE id=?",(tid,)).fetchone()
            if r is None or (r["status"]=="pending" and action=="revoke") or (
                r["status"]=="approved" and action!="revoke") or r["status"] in ("rejected","revoked"):
                raise StoreError("Transfer is not in required state")
            status={"approve":"approved","reject":"rejected","revoke":"revoked"}[action]
            c.execute("UPDATE skill_transfers SET status=?,updated_at=? WHERE id=?",(status,now(),tid))
            self.store._audit(c,"skill_transfer_"+status,tid,None,None)
        return {"id":tid,"status":status,"applied_to_model":False}

    def transfers(self)->list[dict]:
        with self.store.db() as c:
            rows=c.execute("SELECT * FROM skill_transfers ORDER BY created_at DESC,id DESC LIMIT 100").fetchall()
        return [dict(r) for r in rows]

    def evolution_diary(self)->list[dict]:
        allowed={"growth_task_done","growth_review_accepted","growth_review_dismissed",
                 "strategy_approved","strategy_rolled_back","skill_transfer_approved",
                 "academy_assessment_recorded","goal_approved","goal_completed","change_approved",
                 "change_rolled_back","episode_approved"}
        return [{"event":r["action"],"id":r["subject"],"at":r["created_at"]}
                for r in self.store.audit_log(100) if r["action"] in allowed]


class BackgroundLearningLoop:
    """One opt-in thread in the existing Python API process; clean shutdown."""
    def __init__(self,growth:GrowthV5,interval:int=25):
        self.growth=growth
        self.interval=max(20,int(interval))
        self.stop_event=threading.Event()
        self.thread:threading.Thread|None=None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.growth.recover()
        self.thread=threading.Thread(target=self._loop,name="SoryukaLocalGrowth",daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop_event.is_set():
            if self.growth.cognition.permission("background_local"):
                try:
                    # Reclaim expired leases on *every* cycle, including after a quick restart.
                    self.growth.recover()
                    self.growth.schedule_cycle()
                    self.growth.run_once()
                except Exception:
                    # Does not crash API; no private user text in external logs.
                    pass
            self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
