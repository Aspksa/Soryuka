"""Soryuka 4.0: opt-in local memory relationships and supervised academy.

Adds capabilities to the ONE existing SoulStore SQLite database and SoryukaEngine.
No model, web access, background work or external dependencies.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import uuid

from .soul_store import SoulStore, StoreError, SKILLS, check_id, clean, new_id, now

CONFIG = Path(__file__).resolve().parents[1] / "character" / "living_personality_v4.json"
SELECTED = [11,12,13,14,15,16,17,21,22,23,24,25,26,27,30,31,32,33,34,35,36,37,38,39,40]
RELATIONS = {"related", "supports", "precedes", "same_topic", "contradicts"}
SYMMETRIC = {"related", "same_topic", "contradicts"}
STOP = {"и", "а", "но", "он", "она", "оно", "это", "этот", "та", "те", "у", "в", "на", "из", "для",
        "я", "мы", "ты", "мне", "моя", "мой", "моё", "мои", "что", "когда", "как", "по", "с",
        "so", "the", "a", "an", "i", "you", "my", "is", "am", "are"}
NEGATIONS = {"не", "нет", "без", "никогда", "not", "never", "no"}
SYNONYMS = {"нравится":"люблю", "обожаю":"люблю", "любимый":"люблю",
            "нравятся":"люблю", "нелюблю":"не люблю", "понравилось":"люблю"}
DDL = """
CREATE TABLE IF NOT EXISTS memory_links (
 source_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
 target_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
 relation TEXT NOT NULL CHECK (relation IN ('related','supports','precedes','same_topic','contradicts')),
 created_at TEXT NOT NULL,
 PRIMARY KEY(source_id,target_id,relation),
 CHECK (source_id <> target_id)
);
CREATE INDEX IF NOT EXISTS ix_memory_links_target ON memory_links(target_id);
CREATE TABLE IF NOT EXISTS memory_provenance (
 memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
 status TEXT NOT NULL CHECK(status IN('owner_confirmed','owner_disputed','source_supplied')),
 source_note TEXT NOT NULL DEFAULT '',
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS academy_assessments (
 id TEXT PRIMARY KEY,
 goal_id TEXT NOT NULL REFERENCES study_goals(id) ON DELETE CASCADE,
 skill TEXT NOT NULL,
 question TEXT NOT NULL,
 reference TEXT NOT NULL,
 answer TEXT NOT NULL,
 passed INTEGER NOT NULL CHECK (passed IN(0,1)),
 review TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_academy_assessments_goal ON academy_assessments(goal_id,created_at);
CREATE TABLE IF NOT EXISTS academy_strategies (
 id TEXT PRIMARY KEY,
 skill TEXT NOT NULL,
 name TEXT NOT NULL,
 baseline REAL NOT NULL CHECK(baseline BETWEEN 0 AND 100),
 trial REAL NOT NULL CHECK(trial BETWEEN 0 AND 100),
 evidence TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('proposed','approved','rejected','rolled_back')),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_academy_strategies_skill ON academy_strategies(skill,status);
"""


def _tokens(value: str, *, without_negation: bool = False) -> set[str]:
    value = value.casefold().replace("ё", "е")
    words = re.findall(r"[^\W_]+", value, flags=re.UNICODE)
    result = set()
    for word in words:
        if word in STOP or (without_negation and word in NEGATIONS):
            continue
        word = SYNONYMS.get(word, word)
        result.add(word)
    return result


def _similarity(left: str, right: str, without_negation: bool = False) -> float:
    a = _tokens(left, without_negation=without_negation)
    b = _tokens(right, without_negation=without_negation)
    return round(len(a & b) / len(a | b), 3) if a and b else 0.0


def _negated(text: str) -> bool:
    return bool(_tokens(text) & NEGATIONS)


def _normalized(value: str) -> str:
    return " ".join(value.casefold().replace("ё","е").strip().split())


class SoulCapabilities:
    def __init__(self, store: SoulStore):
        self.store = store
        v4 = json.loads(CONFIG.read_text(encoding="utf-8"))
        if v4.get("selected_ids") != SELECTED or [x.get("id") for x in v4.get("feature_contracts",[])] != SELECTED:
            raise ValueError("Soryuka 4.0 contract mismatch")
        self.config = v4
        with self.store.db() as db:
            db.executescript(DDL)

    def _one(self, db: sqlite3.Connection, memory_id: str):
        item = db.execute("SELECT id,kind,body,created_at,source FROM memories WHERE id=?",
                          (check_id(memory_id),)).fetchone()
        if item is None:
            raise StoreError("Memory not found")
        return item

    def provenance(self, memory_id: str) -> dict:
        with self.store.db() as db:
            mem = self._one(db, memory_id)
            meta = db.execute("SELECT status,source_note,updated_at FROM memory_provenance WHERE memory_id=?",
                              (mem["id"],)).fetchone()
        if meta is None:
            return {"status":"owner_confirmed","source_note":"",
                    "meaning":"Saved after owner confirmation; this is provenance, not proof of objective truth"}
        return {**dict(meta), "meaning":"User-supplied provenance label; no source independently verified"}

    def set_provenance(self, memory_id: str, status: str, source_note: str, confirmed: bool) -> dict:
        if confirmed is not True:
            raise StoreError("Explicit owner approval required")
        if status not in {"owner_confirmed", "owner_disputed", "source_supplied"}:
            raise StoreError("Unsupported provenance")
        note = clean(source_note, 350) if status == "source_supplied" else ""
        with self.store.db() as db:
            record = self._one(db, memory_id)
            db.execute("""INSERT INTO memory_provenance(memory_id,status,source_note,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(memory_id) DO UPDATE SET status=excluded.status,
                source_note=excluded.source_note,updated_at=excluded.updated_at""",
                (record["id"],status,note,now()))
            self.store._audit(db, "memory_provenance_confirmed",record["id"],None,
                              json.dumps({"status":status}, ensure_ascii=False))
        return self.provenance(memory_id)

    def link(self, source_id: str, target_id: str, relation: str, confirmed: bool) -> dict:
        if confirmed is not True:
            raise StoreError("Explicit confirmation required to connect memories")
        if relation not in RELATIONS:
            raise StoreError("Unsupported relationship")
        left, right = check_id(source_id), check_id(target_id)
        if left == right:
            raise StoreError("A memory cannot be linked to itself")
        if relation in SYMMETRIC:
            left, right = sorted((left,right))
        with self.store.db() as db:
            self._one(db,left); self._one(db,right)
            db.execute("INSERT OR IGNORE INTO memory_links(source_id,target_id,relation,created_at) VALUES(?,?,?,?)",
                       (left,right,relation,now()))
            self.store._audit(db,"memory_link_confirmed",left,None,json.dumps({"other_id":right,"relation":relation}))
        return {"source_id":left,"target_id":right,"relation":relation}

    def links(self, memory_id: str | None = None, limit: int = 100) -> list[dict]:
        with self.store.db() as db:
            if memory_id:
                mid = check_id(memory_id)
                rows = db.execute("""SELECT source_id,target_id,relation,created_at FROM memory_links
                    WHERE source_id=? OR target_id=? ORDER BY created_at DESC LIMIT ?""",
                    (mid,mid,max(1,min(int(limit),150)))).fetchall()
            else:
                rows = db.execute("""SELECT source_id,target_id,relation,created_at FROM memory_links
                    ORDER BY created_at DESC LIMIT ?""",
                    (max(1,min(int(limit),150)),)).fetchall()
        return [dict(row) for row in rows]

    def unlink(self, source_id: str, target_id: str, relation: str) -> bool:
        if relation not in RELATIONS:
            raise StoreError("Unsupported relationship")
        a,b = check_id(source_id),check_id(target_id)
        if relation in SYMMETRIC:
            a,b = sorted((a,b))
        with self.store.db() as db:
            cur=db.execute("DELETE FROM memory_links WHERE source_id=? AND target_id=? AND relation=?",(a,b,relation))
            if cur.rowcount:
                self.store._audit(db,"memory_link_removed",a,None,json.dumps({"other_id":b,"relation":relation}))
        return bool(cur.rowcount)

    def memory_index(self, query: str, limit: int = 12) -> list[dict]:
        text = clean(query, 250)
        toks = _tokens(text, without_negation=True)
        if not toks:
            return []
        with self.store.db() as db:
            rows=db.execute("""SELECT m.id,m.kind,m.body,m.created_at,
                COALESCE(p.status,'owner_confirmed') AS provenance
                FROM memories m LEFT JOIN memory_provenance p ON p.memory_id=m.id
                ORDER BY m.created_at DESC,m.id DESC LIMIT 1500""").fetchall()
        scores=[]
        for row in rows:
            score=_similarity(text,row["body"],without_negation=True)
            if score <= 0:
                continue
            scores.append({**dict(row),"score":score,"match_method":"local_token_similarity"})
        scores.sort(key=lambda x:(x["score"],x["created_at"],x["id"]),reverse=True)
        return scores[:max(1,min(int(limit),30))]

    def review_candidates(self, memory_id: str, limit: int = 12) -> list[dict]:
        with self.store.db() as db:
            current=self._one(db,memory_id)
            records=db.execute("SELECT id,kind,body FROM memories WHERE id<>? ORDER BY created_at DESC LIMIT 1000",
                               (current["id"],)).fetchall()
        found=[]
        for candidate in records:
            similarity=_similarity(current["body"],candidate["body"],without_negation=True)
            if similarity < 0.62:
                continue
            # This is a hint, not a claim that the underlying statements are equal/false.
            kind=("possible_conflict" if _negated(current["body"]) != _negated(candidate["body"])
                  else "possible_duplicate")
            found.append({"memory_id":candidate["id"],"body":candidate["body"],
                          "similarity":similarity,"kind":kind,
                          "verified":False,"needs_owner_review":True})
        found.sort(key=lambda x:(x["similarity"],x["memory_id"]),reverse=True)
        return found[:max(1,min(int(limit),30))]

    def merge_memories(self, keep_id: str, remove_id: str, confirmed: bool) -> dict:
        """Owner-approved consolidation. No LLM semantics, and no copying private text to audit."""
        if confirmed is not True:
            raise StoreError("Owner confirmation required to merge memories")
        keep,remove=check_id(keep_id),check_id(remove_id)
        if keep==remove:
            raise StoreError("Choose distinct memories")
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            first=self._one(db,keep); second=self._one(db,remove)
            similarity=_similarity(first["body"],second["body"],without_negation=True)
            if similarity<0.62 or _negated(first["body"])!=_negated(second["body"]):
                raise StoreError("Items are not safe similarity candidates; retain both")
            links=db.execute("""SELECT source_id,target_id,relation FROM memory_links
                WHERE source_id=? OR target_id=?""",(remove,remove)).fetchall()
            for l in links:
                a=keep if l["source_id"]==remove else l["source_id"]
                b=keep if l["target_id"]==remove else l["target_id"]
                if a==b:continue
                if l["relation"] in SYMMETRIC:a,b=sorted((a,b))
                db.execute("""INSERT OR IGNORE INTO memory_links(source_id,target_id,relation,created_at)
                              VALUES(?,?,?,?)""",(a,b,l["relation"],now()))
            db.execute("DELETE FROM memories WHERE id=?",(remove,))
            self.store._audit(db,"memory_merge_approved",keep,None,
                              json.dumps({"removed_id":remove,"similarity":similarity}))
        return {"kept_id":keep,"removed_id":remove,"similarity":similarity}

    def chronology(self, limit: int = 60) -> list[dict]:
        maxn=max(1,min(int(limit),150))
        with self.store.db() as db:
            rows=db.execute("""
              SELECT id,kind AS category,body,created_at,0 AS fiction,'owner_confirmed' AS source
              FROM memories
              UNION ALL
              SELECT id,'story_event' AS category,body,created_at,fiction,
                     CASE WHEN fiction=1 THEN 'approved_fiction' ELSE 'owner_confirmed_real_event' END AS source
              FROM story_events
              ORDER BY created_at DESC,id DESC LIMIT ?""",(maxn,)).fetchall()
        return [dict(r) for r in rows]

    def relationship_context(self, limit: int = 8) -> dict:
        """Read-only opt-in moments, traditions and clearly labeled fiction."""
        with self.store.db() as db:
            moments=db.execute("""SELECT id,kind,body FROM memories WHERE kind IN ('moment','tradition')
                ORDER BY created_at DESC,id DESC LIMIT ?""",(max(1,min(limit,12)),)).fetchall()
            fiction=db.execute("""SELECT id,body FROM story_events WHERE fiction=1
                ORDER BY created_at DESC,id DESC LIMIT ?""",(max(1,min(limit,12)),)).fetchall()
        return {"approved_memories":[dict(x) for x in moments],
                "fictional_events":[dict(x) for x in fiction]}

    def compliment_reaction(self, conversation_turns: int) -> dict:
        reactions=self.config["compliments"]
        if type(conversation_turns) is not int or conversation_turns<0:
            raise StoreError("Invalid conversation index")
        row=reactions[conversation_turns%len(reactions)]
        return dict(row)

    def assessment(self, goal_id: str, question: str, reference: str,
                   answer: str, confirmed: bool, review: str = "") -> dict:
        """Record a manual, exact-text reference check, not model intelligence training."""
        if confirmed is not True:
            raise StoreError("Assessment must be explicitly submitted")
        gid=check_id(goal_id)
        question=clean(question,500); reference=clean(reference,1500); answer=clean(answer,1500)
        comment=clean(review,700) if review.strip() else ""
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            goal=db.execute("SELECT skill,status FROM study_goals WHERE id=?",(gid,)).fetchone()
            if goal is None or goal["status"]!="approved":
                raise StoreError("Only approved study goals can be assessed")
            passed=int(_normalized(reference)==_normalized(answer))
            record={"id":new_id(),"goal_id":gid,"skill":goal["skill"],"question":question,
                    "reference":reference,"answer":answer,"passed":passed,"review":comment,"created_at":now()}
            db.execute("""INSERT INTO academy_assessments
                (id,goal_id,skill,question,reference,answer,passed,review,created_at)
                VALUES(:id,:goal_id,:skill,:question,:reference,:answer,:passed,:review,:created_at)""",record)
            self.store._audit(db,"academy_assessment_recorded",record["id"],None,
                              json.dumps({"skill":goal["skill"],"passed":bool(passed)}))
        return {k:v for k,v in record.items() if k!="reference"} | {"check_method":"normalized_exact_match"}

    def skill_map(self) -> list[dict]:
        goals={skill:Counter() for skill in sorted(SKILLS)}
        for g in self.store.goals():
            goals[g["skill"]][g["status"]]+=1
        ratings={row["skill"]:row for row in self.store.skill_metrics()}
        with self.store.db() as db:
            rows=db.execute("""SELECT skill,COUNT(*) AS tests,
                SUM(passed) AS passed FROM academy_assessments GROUP BY skill""").fetchall()
        checks={r["skill"]:dict(r) for r in rows}
        return [{"skill":skill,"goals":dict(goals[skill]),"owner_ratings":ratings.get(skill),
                 "reference_checks":checks.get(skill,{"tests":0,"passed":0}),
                 "no_model_weight_training":True} for skill in sorted(SKILLS)]

    def assessments(self, goal_id: str | None = None, limit: int = 30) -> list[dict]:
        with self.store.db() as db:
            if goal_id:
                rows=db.execute("""SELECT id,goal_id,skill,question,answer,passed,review,created_at
                    FROM academy_assessments WHERE goal_id=? ORDER BY created_at DESC,id DESC LIMIT ?""",
                    (check_id(goal_id),max(1,min(int(limit),100)))).fetchall()
            else:
                rows=db.execute("""SELECT id,goal_id,skill,question,answer,passed,review,created_at
                    FROM academy_assessments ORDER BY created_at DESC,id DESC LIMIT ?""",
                    (max(1,min(int(limit),100)),)).fetchall()
        return [dict(x) for x in rows]

    def propose_strategy(self, skill: str, name: str, baseline: float,
                         trial: float, evidence: str, confirmed: bool) -> dict:
        if confirmed is not True:
            raise StoreError("Owner must explicitly supply comparison evidence")
        if skill not in SKILLS:
            raise StoreError("Unknown skill")
        if any(type(v) not in (int,float) or not (0<=v<=100) for v in (baseline,trial)):
            raise StoreError("Benchmarks must be finite numbers from 0 to 100")
        record={"id":new_id(),"skill":skill,"name":clean(name,120),
                "baseline":float(baseline),"trial":float(trial),"evidence":clean(evidence,500),
                "status":"proposed","created_at":now(),"updated_at":now()}
        with self.store.db() as db:
            db.execute("""INSERT INTO academy_strategies
                (id,skill,name,baseline,trial,evidence,status,created_at,updated_at)
                VALUES (:id,:skill,:name,:baseline,:trial,:evidence,:status,:created_at,:updated_at)""",record)
            self.store._audit(db,"strategy_proposed",record["id"],None,
                              json.dumps({"skill":skill,"score_delta":round(trial-baseline,3)}))
        return record

    def strategy_action(self, strategy_id: str, action: str) -> dict:
        if action not in {"approve","reject","rollback"}:
            raise StoreError("Unknown strategy decision")
        sid=check_id(strategy_id)
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old=db.execute("SELECT * FROM academy_strategies WHERE id=?",(sid,)).fetchone()
            if old is None:
                raise StoreError("Strategy not found")
            if action in ("approve","reject") and old["status"]!="proposed":
                raise StoreError("Only proposed strategies can be decided")
            if action=="rollback" and old["status"]!="approved":
                raise StoreError("Only approved strategies can be rolled back")
            # These are recorded recommendations; no model algorithm is silently mutated.
            new={"approve":"approved","reject":"rejected","rollback":"rolled_back"}[action]
            db.execute("UPDATE academy_strategies SET status=?,updated_at=? WHERE id=?",(new,now(),sid))
            self.store._audit(db,"strategy_"+new,sid,None,
                              json.dumps({"skill":old["skill"],"decision":new}))
            return {"id":sid,"skill":old["skill"],"status":new,"applied_to_model":False}

    def strategies(self, limit: int = 60) -> list[dict]:
        with self.store.db() as db:
            rows=db.execute("""SELECT id,skill,name,baseline,trial,evidence,status,created_at
                FROM academy_strategies ORDER BY created_at DESC,id DESC LIMIT ?""",
                (max(1,min(int(limit),100)),)).fetchall()
        return [dict(x) for x in rows]

    def development_council(self) -> list[dict]:
        """Read-only evidence-aware recommendations. Does not create or approve goals."""
        candidates=[{**row,"source":"owner_rating"} for row in self.store.recommend_goals()]
        active={g["skill"] for g in self.store.goals() if g["status"] in ("proposed","approved")}
        with self.store.db() as db:
            rows=db.execute("""SELECT skill,COUNT(*) AS attempts,SUM(passed) AS passed
                FROM academy_assessments GROUP BY skill""").fetchall()
        for row in rows:
            if row["attempts"]<2 or row["passed"]==row["attempts"] or row["skill"] in active:
                continue
            if any(c["skill"]==row["skill"] for c in candidates):
                continue
            candidates.append({"skill":row["skill"],"title":"Разбор ошибок и контрольные задания",
                 "criterion":"Повторить минимум 3 проверяемых задания с правильными эталонами",
                 "reason":f"{row['passed']} из {row['attempts']} локальных проверок совпали с эталоном",
                 "source":"local_reference_checks"})
        return candidates

    def diary(self, limit: int = 50) -> list[dict]:
        # Reuse the single existing audit: no parallel diary DB or unverifiable internal thoughts.
        actions={"academy_assessment_recorded","goal_approved","goal_completed","goal_rejected",
                 "strategy_proposed","strategy_approved","strategy_rejected","strategy_rolled_back",
                 "feedback_received","feedback_deleted","change_approved","change_rolled_back"}
        return [{"action":e["action"],"subject":e["subject"],"created_at":e["created_at"]}
                for e in self.store.audit_log(100) if e["action"] in actions][:max(1,min(int(limit),100))]
