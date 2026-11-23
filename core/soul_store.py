"""SQLite storage for Soryuka 3.0: conversations, explicitly approved memories, learning and audits.

Single local DB; no background worker, model, implicit personal memory, or unapproved writes.
Uses Python standard library only.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid

BASE = Path(__file__).resolve().parents[1]
KINDS = {"moment", "preference", "tradition", "event", "fact"}
SKILLS = {"языки", "программирование", "дизайн", "логика", "общение", "творчество"}
ADAPTIVE = {"reply_length", "humor_level", "formality", "affection_level"}
LIMITS = {
    "reply_length": {"brief", "normal", "detailed"},
    "humor_level": {"low", "medium", "high"},
    "formality": {"low", "medium", "high"},
    "affection_level": {"low", "medium", "high"},
}
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES sessions(id),
 role TEXT NOT NULL CHECK (role IN ('user','assistant')),
 body TEXT NOT NULL, event TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_session_id ON messages(session_id,id);
CREATE TABLE IF NOT EXISTS memories (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
 source TEXT NOT NULL DEFAULT 'owner_confirmed'
);
CREATE TABLE IF NOT EXISTS story_events (
 id TEXT PRIMARY KEY, body TEXT NOT NULL, fiction INTEGER NOT NULL CHECK(fiction IN(0,1)),
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS study_goals (
 id TEXT PRIMARY KEY, skill TEXT NOT NULL, title TEXT NOT NULL, criterion TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN('proposed','approved','completed','rejected')),
 evidence TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
 id TEXT PRIMARY KEY, skill TEXT NOT NULL, rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
 note TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
 id TEXT PRIMARY KEY, field TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL,
 reason TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN('pending','approved','rejected','rolled_back')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preferences (
 field TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, subject TEXT NOT NULL,
 before_json TEXT, after_json TEXT, created_at TEXT NOT NULL
);
"""


class StoreError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value: object, limit: int = 1500) -> str:
    if not isinstance(value, str):
        raise StoreError("Expected text")
    s = value.strip()
    if not s or len(s) > limit:
        raise StoreError(f"Text must be 1–{limit} characters")
    return s


def check_id(value: object) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise StoreError("Invalid UUID") from exc


def new_id() -> str:
    return str(uuid.uuid4())


class SoulStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("SORYUKA_DB_PATH") or BASE / "data" / "soryuka.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as conn:
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def db(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def as_dicts(rows):
        return [dict(row) for row in rows]

    def ensure_session(self, session_id: str | None = None) -> str:
        sid = check_id(session_id) if session_id else new_id()
        with self.db() as conn:
            conn.execute("INSERT OR IGNORE INTO sessions (id,created_at,updated_at) VALUES (?,?,?)",
                         (sid, now(), now()))
        return sid

    def history(self, session_id: str, limit: int = 12) -> list[dict]:
        sid = check_id(session_id)
        with self.db() as conn:
            rows = conn.execute(
                "SELECT role,body,event,created_at FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (sid, max(1, min(int(limit), 30)))).fetchall()
        return list(reversed(self.as_dicts(rows)))

    def sessions(self, limit: int = 40) -> list[dict]:
        with self.db() as conn:
            return self.as_dicts(conn.execute(
                "SELECT id,created_at,updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(int(limit), 80)),)).fetchall())

    def save_exchange(self, session_id: str, user_text: str, answer: str, event: str) -> None:
        sid = check_id(session_id)
        clean(user_text, 6000)
        clean(answer, 24000)
        with self.db() as conn:
            if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
                raise StoreError("Session not found")
            stamp = now()
            conn.executemany(
                "INSERT INTO messages(session_id,role,body,event,created_at) VALUES(?,?,?,?,?)",
                [(sid,"user",user_text,event,stamp),(sid,"assistant",answer,event,stamp)])
            conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (stamp,sid))

    def add_memory(self, kind: str, body: str, confirmed: bool) -> dict:
        if confirmed is not True:
            raise StoreError("Explicit owner confirmation required to save memory")
        if kind not in KINDS:
            raise StoreError("Unsupported memory kind")
        record = {"id":new_id(),"kind":kind,"body":clean(body),"created_at":now()}
        with self.db() as conn:
            conn.execute("INSERT INTO memories(id,kind,body,created_at) VALUES(?,?,?,?)",
                         (record["id"],record["kind"],record["body"],record["created_at"]))
            self._audit(conn, "memory_approved", record["id"], None, json.dumps({"kind":kind}))
        return record

    def list_memories(self, limit: int = 8) -> list[dict]:
        with self.db() as conn:
            return self.as_dicts(conn.execute(
                "SELECT id,kind,body,created_at FROM memories ORDER BY created_at DESC,id DESC LIMIT ?",
                (max(1,min(int(limit),50)),)).fetchall())

    def delete_memory(self, memory_id: str) -> bool:
        mid=check_id(memory_id)
        with self.db() as conn:
            old=conn.execute("SELECT kind,body FROM memories WHERE id=?", (mid,)).fetchone()
            if old is None:
                return False
            conn.execute("DELETE FROM memories WHERE id=?", (mid,))
            self._audit(conn,"memory_deleted",mid,json.dumps({"kind":old["kind"]}),None)
        return True

    def add_event(self, body: str, fiction: bool, confirmed: bool) -> dict:
        if confirmed is not True or not isinstance(fiction,bool):
            raise StoreError("Event must be explicitly confirmed and labeled fiction or real")
        row={"id":new_id(),"body":clean(body),"fiction":int(fiction),"created_at":now()}
        with self.db() as conn:
            conn.execute("INSERT INTO story_events(id,body,fiction,created_at) VALUES(?,?,?,?)",tuple(row.values()))
            self._audit(conn,"event_confirmed",row["id"],None,json.dumps({"fiction":row["fiction"]}))
        return row

    def timeline(self, limit: int = 50) -> list[dict]:
        with self.db() as conn:
            return self.as_dicts(conn.execute(
                "SELECT id,body,fiction,created_at FROM story_events ORDER BY created_at DESC,id DESC LIMIT ?",
                (max(1,min(int(limit),100)),)).fetchall())

    def delete_session(self, session_id: str) -> bool:
        sid=check_id(session_id)
        with self.db() as conn:
            exists=conn.execute("SELECT 1 FROM sessions WHERE id=?",(sid,)).fetchone()
            if not exists:
                return False
            conn.execute("DELETE FROM messages WHERE session_id=?",(sid,))
            conn.execute("DELETE FROM sessions WHERE id=?",(sid,))
            self._audit(conn,"session_deleted",sid,None,None)
        return True

    def delete_event(self, event_id: str) -> bool:
        eid=check_id(event_id)
        with self.db() as conn:
            old=conn.execute("SELECT fiction FROM story_events WHERE id=?",(eid,)).fetchone()
            if old is None:
                return False
            conn.execute("DELETE FROM story_events WHERE id=?",(eid,))
            self._audit(conn,"event_deleted",eid,json.dumps({"fiction":old["fiction"]}),None)
        return True

    def delete_feedback(self, feedback_id: str) -> bool:
        fid=check_id(feedback_id)
        with self.db() as conn:
            old=conn.execute("SELECT skill,rating FROM feedback WHERE id=?",(fid,)).fetchone()
            if old is None:
                return False
            conn.execute("DELETE FROM feedback WHERE id=?",(fid,))
            self._audit(conn,"feedback_deleted",fid,json.dumps(dict(old),ensure_ascii=False),None)
        return True

    def create_backup(self, confirmed: bool) -> dict:
        """Explicit, consistent online SQLite backup; never started in background."""
        if confirmed is not True:
            raise StoreError("Explicit confirmation required to create backup")
        dest=self.path.parent/"backups"
        dest.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target=dest/f"soryuka-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
        # A sqlite3.Connection context manager does NOT close the connection;
        # explicitly close source and destination to release Windows file locks.
        with closing(sqlite3.connect(str(self.path),timeout=15)) as original:
            with closing(sqlite3.connect(str(target),timeout=15)) as backup:
                original.backup(backup)
        with self.db() as conn:
            self._audit(conn,"backup_created",target.name,None,None)
        return {"filename":target.name,"path":str(target),"created_at":now()}

    def propose_goal(self, skill: str, title: str, criterion: str) -> dict:
        if skill not in SKILLS:
            raise StoreError("Unsupported skill area")
        row={"id":new_id(),"skill":skill,"title":clean(title,160),"criterion":clean(criterion,500),
             "status":"proposed","created_at":now(),"updated_at":now()}
        with self.db() as conn:
            conn.execute("INSERT INTO study_goals(id,skill,title,criterion,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                         tuple(row.values()))
        return row

    def goals(self) -> list[dict]:
        with self.db() as conn:
            return self.as_dicts(conn.execute(
                "SELECT * FROM study_goals ORDER BY created_at DESC,id DESC LIMIT 100").fetchall())

    def goal_action(self, goal_id: str, action: str, evidence: str = "") -> dict:
        if action not in {"approve","reject","complete"}:
            raise StoreError("Invalid goal action")
        gid=check_id(goal_id)
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old=conn.execute("SELECT * FROM study_goals WHERE id=?", (gid,)).fetchone()
            if old is None:
                raise StoreError("Goal not found")
            status=old["status"]
            expected="proposed" if action in {"approve","reject"} else "approved"
            if status!=expected:
                raise StoreError("Goal not in required state")
            outcome={"approve":"approved","reject":"rejected","complete":"completed"}[action]
            proof=clean(evidence,1500) if action=="complete" else None
            conn.execute("UPDATE study_goals SET status=?,evidence=?,updated_at=? WHERE id=?",
                         (outcome,proof,now(),gid))
            self._audit(conn,"goal_"+outcome,gid,json.dumps(dict(old),ensure_ascii=False),
                        json.dumps({"status":outcome,"evidence":proof},ensure_ascii=False))
            return dict(conn.execute("SELECT * FROM study_goals WHERE id=?", (gid,)).fetchone())

    def add_feedback(self, skill: str, rating: int, note: str) -> dict:
        if skill not in SKILLS or type(rating) is not int or not (1<=rating<=5):
            raise StoreError("Feedback skill or rating invalid")
        row={"id":new_id(),"skill":skill,"rating":rating,"note":clean(note,1500),"created_at":now()}
        with self.db() as conn:
            conn.execute("INSERT INTO feedback(id,skill,rating,note,created_at) VALUES(?,?,?,?,?)",tuple(row.values()))
            self._audit(conn,"feedback_received",row["id"],None,json.dumps({"skill":skill,"rating":rating},ensure_ascii=False))
        return row

    def skill_metrics(self) -> list[dict]:
        with self.db() as conn:
            return self.as_dicts(conn.execute("""
             SELECT skill,COUNT(*) AS rating_count,ROUND(AVG(rating),2) AS mean_owner_rating,
                    MIN(rating) AS minimum_rating,MAX(rating) AS maximum_rating
             FROM feedback GROUP BY skill ORDER BY skill
            """).fetchall())

    def recommend_goals(self) -> list[dict]:
        """On-demand, read-only learning suggestions from observed owner ratings.

        At least two ratings and an average <= 3.5 are needed; never invent a score.
        Suggestions are NOT enrolled automatically, never run in background, cost nothing.
        """
        examples={
            "языки":("Точность перевода","Проверить 10 коротких фраз с ответами"),
            "программирование":("Надёжность программных решений","Пройти 5 проверяемых задач без регрессий"),
            "дизайн":("Читаемость интерфейса","Проверить 5 экранов по чек-листу UX"),
            "логика":("Проверяемость рассуждений","Разобрать 5 задач и проверить выводы"),
            "общение":("Разнообразие и точность речи","Оценить 5 диалогов по ясности и отсутствию повторов"),
            "творчество":("Качество творческих предложений","Получить оценки пяти авторских работ")
        }
        active={x["skill"] for x in self.goals() if x["status"] in ("proposed","approved")}
        suggestions=[]
        for metric in self.skill_metrics():
            skill=metric["skill"]
            if metric["rating_count"]>=2 and metric["mean_owner_rating"]<=3.5 and skill not in active:
                title,criterion=examples[skill]
                suggestions.append({"skill":skill,"title":title,"criterion":criterion,
                                    "reason":f"{metric['rating_count']} оценок; средняя {metric['mean_owner_rating']}/5"})
        return suggestions

    def recent_feedback(self, limit:int=30)->list[dict]:
        with self.db() as conn:
            return self.as_dicts(conn.execute(
                "SELECT * FROM feedback ORDER BY created_at DESC,id DESC LIMIT ?",
                (max(1,min(int(limit),100)),)).fetchall())

    @staticmethod
    def _audit(conn, action, subject, before, after):
        conn.execute("INSERT INTO audit(action,subject,before_json,after_json,created_at) VALUES (?,?,?,?,?)",
                     (action,subject,before,after,now()))

    def audit_log(self, limit:int=50)->list[dict]:
        with self.db() as conn:
            return self.as_dicts(conn.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?",
                (max(1,min(int(limit),100)),)).fetchall())

    def propose_change(self, field: str, value: str, reason: str) -> dict:
        if field not in ADAPTIVE or value not in LIMITS[field]:
            raise StoreError("Only allowlisted adaptive preferences can change; canon requires a reviewed PR")
        reason=clean(reason,500)
        with self.db() as conn:
            row=conn.execute("SELECT value_json FROM preferences WHERE field=?", (field,)).fetchone()
            before=row["value_json"] if row else None
            after=json.dumps(value,ensure_ascii=False)
            if before==after:
                raise StoreError("Value already active")
            if conn.execute("SELECT 1 FROM proposals WHERE field=? AND status='pending'",(field,)).fetchone():
                raise StoreError("Pending change already exists for this field")
            record={"id":new_id(),"field":field,"before_json":before,"after_json":after,
                    "reason":reason,"status":"pending","created_at":now(),"updated_at":now()}
            conn.execute("INSERT INTO proposals(id,field,before_json,after_json,reason,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                         tuple(record.values()))
            self._audit(conn,"change_proposed",record["id"],before,after)
        return record

    def proposals(self) -> list[dict]:
        with self.db() as conn:
            return self.as_dicts(conn.execute(
                "SELECT * FROM proposals ORDER BY created_at DESC,id DESC LIMIT 100").fetchall())

    def preferences(self) -> dict:
        with self.db() as conn:
            rows=conn.execute("SELECT field,value_json FROM preferences").fetchall()
        return {r["field"]:json.loads(r["value_json"]) for r in rows}

    def change_action(self, proposal_id: str, action: str) -> dict:
        if action not in {"approve","reject","rollback"}:
            raise StoreError("Unknown action")
        pid=check_id(proposal_id)
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row=conn.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
            if row is None:
                raise StoreError("Proposal not found")
            old=dict(row)
            if action in {"approve","reject"} and row["status"]!="pending":
                raise StoreError("Only pending changes can be decided")
            if action=="rollback" and row["status"]!="approved":
                raise StoreError("Only approved changes can be rolled back")
            current=conn.execute("SELECT value_json FROM preferences WHERE field=?",(row["field"],)).fetchone()
            present=current["value_json"] if current else None
            if action=="approve":
                if present!=row["before_json"]:
                    raise StoreError("Preference changed since proposal; review again")
                conn.execute("INSERT INTO preferences(field,value_json,updated_at) VALUES(?,?,?) "
                             "ON CONFLICT(field) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                             (row["field"],row["after_json"],now()))
                target="approved"
            elif action=="reject":
                target="rejected"
            else:
                if present!=row["after_json"]:
                    raise StoreError("Cannot roll back: a later change superseded this preference")
                if row["before_json"] is None:
                    conn.execute("DELETE FROM preferences WHERE field=?",(row["field"],))
                else:
                    conn.execute("UPDATE preferences SET value_json=?,updated_at=? WHERE field=?",
                                 (row["before_json"],now(),row["field"]))
                target="rolled_back"
            conn.execute("UPDATE proposals SET status=?,updated_at=? WHERE id=?",(target,now(),pid))
            self._audit(conn,"change_"+target,pid,json.dumps(old,ensure_ascii=False),
                        json.dumps({"field":row["field"],"status":target},ensure_ascii=False))
            return dict(conn.execute("SELECT * FROM proposals WHERE id=?",(pid,)).fetchone())
