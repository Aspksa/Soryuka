"""Soryuka 5.0: evidence-aware local cognition; optional, explicit Cloud.ru embeddings.

Reuses one SoulStore SQLite and SoryukaEngine. All facts, episodes, causal links
and vector indexing require explicit owner action. No background cloud requests.
Reasoning scores are retrieval indicators, never proof of a claim or of consciousness.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

from .soul_store import SoulStore, StoreError, check_id, clean, new_id, now
from .capabilities_v4 import SoulCapabilities

ROOT=Path(__file__).resolve().parents[1]
CONTRACT=ROOT/"character"/"living_personality_v5.json"
DEFAULT_EMBED_MODEL="Qwen/Qwen3-Embedding-0.6B"
EMBED_URL="https://foundation-models.api.cloud.ru/v1/embeddings"
HEURISTIC_PATTERNS=[
    (re.compile(r"(?:^|[.!?]\s*)(я люблю|мне нравится|я предпочитаю)\b",re.I),"preference"),
    (re.compile(r"(?:^|[.!?]\s*)(моя цель|мы решили|я работаю)\b",re.I),"fact"),
    (re.compile(r"(?:^|[.!?]\s*)(запомни|помни, что)\b",re.I),"fact"),
]
SCHEMA="""
CREATE TABLE IF NOT EXISTS cognitive_settings (
 name TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN(0,1)),
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_episodes (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 digest TEXT NOT NULL, source_messages INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS causal_links (
 cause_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
 effect_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
 evidence_note TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(cause_id,effect_id),CHECK(cause_id<>effect_id)
);
CREATE TABLE IF NOT EXISTS embedding_cache (
 model TEXT NOT NULL, text_sha256 TEXT NOT NULL, vector_json TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(model,text_sha256)
);
CREATE TABLE IF NOT EXISTS memory_embeddings (
 memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
 model TEXT NOT NULL, text_sha256 TEXT NOT NULL, indexed_at TEXT NOT NULL,
 PRIMARY KEY(memory_id,model)
);
"""


def _hash(value: str)->str:
    return sha256(value.encode("utf-8")).hexdigest()


def _cosine(a:list[float], b:list[float])->float:
    if len(a)!=len(b) or not a:
        return 0.0
    na=math.sqrt(sum(x*x for x in a));nb=math.sqrt(sum(x*x for x in b))
    if na==0 or nb==0:
        return 0.0
    return sum(x*y for x,y in zip(a,b))/(na*nb)


class EmbeddingService:
    """Network adapter. Calls only from explicit API operation with user permission."""
    def __init__(self,key:str|None=None,model:str|None=None):
        self.key=key if key is not None else os.getenv("CLOUD_RU_API_KEY","")
        self.model=model if model is not None else os.getenv("CLOUD_RU_EMBEDDING_MODEL",DEFAULT_EMBED_MODEL)
    def ready(self)->bool:
        return bool(self.key and self.model)
    def encode(self,text:str)->list[float]:
        text=clean(text,1500)
        if not self.ready():
            raise StoreError("CLOUD_RU_API_KEY and embedding model are required")
        req=urllib.request.Request(EMBED_URL,data=json.dumps(
            {"input":text,"model":self.model},ensure_ascii=False).encode("utf-8"),
            headers={"Authorization":"Bearer "+self.key,"Content-Type":"application/json"},method="POST")
        try:
            with urllib.request.urlopen(req,timeout=30) as response:
                doc=json.loads(response.read(800_000))
        except urllib.error.HTTPError as exc:
            raise StoreError(f"Cloud.ru embeddings HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise StoreError("Cloud.ru embeddings not reachable") from None
        except (ValueError,UnicodeDecodeError) as exc:
            raise StoreError("Invalid embeddings response") from exc
        try:
            arr=doc["data"][0]["embedding"]
            if not 1<=len(arr)<=4096 or any(type(x) not in (int,float) or not math.isfinite(x) for x in arr):
                raise ValueError("Invalid numeric vector")
            return [float(v) for v in arr]
        except (IndexError,KeyError,TypeError,ValueError) as exc:
            raise StoreError("Unexpected Cloud.ru embedding schema") from exc


class CognitiveV5:
    def __init__(self,store:SoulStore,cap:SoulCapabilities|None=None,embed:EmbeddingService|None=None):
        self.store=store
        self.cap=cap or SoulCapabilities(store)
        self.embed=embed or EmbeddingService()
        self.contract=json.loads(CONTRACT.read_text(encoding="utf-8"))
        expected=[1,2,3,4,5,8,9,11,12,13,14,15,16,17,18,21,25,28,30,31,32,33,34,35,36,37,38,39,40,42,44,47]
        if self.contract.get("selected_ids")!=expected:
            raise ValueError("Unexpected Soryuka 5.0 feature selection")
        with store.db() as conn:
            conn.executescript(SCHEMA)

    def permission(self,name:str)->bool:
        if name not in ("background_local","fact_suggestions","cloud_embeddings"):
            raise StoreError("Unknown permission")
        with self.store.db() as c:
            row=c.execute("SELECT enabled FROM cognitive_settings WHERE name=?",(name,)).fetchone()
        return bool(row["enabled"]) if row else False

    def set_permission(self,name:str,enabled:bool,confirmed:bool)->dict:
        if name not in ("background_local","fact_suggestions","cloud_embeddings"):
            raise StoreError("Unknown permission")
        if type(enabled) is not bool or confirmed is not True:
            raise StoreError("Owner must explicitly confirm the permission")
        with self.store.db() as c:
            c.execute("""INSERT INTO cognitive_settings(name,enabled,updated_at) VALUES(?,?,?)
                ON CONFLICT(name) DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at""",
                (name,int(enabled),now()))
            self.store._audit(c,"cognitive_permission",name,None,json.dumps({"enabled":enabled}))
        return {"name":name,"enabled":enabled}

    def permissions(self)->dict:
        return {name:self.permission(name) for name in ("background_local","fact_suggestions","cloud_embeddings")}

    def suggested_facts(self,user_text:str)->list[dict]:
        """Suggest short user-stated facts; never store, verify, or infer hidden traits."""
        if not self.permission("fact_suggestions"):
            return []
        text=clean(user_text,6000)
        sentences=re.split(r"[\n]+|(?<=[.!?])\s+",text)
        suggestions=[]
        for sentence in sentences[:12]:
            s=sentence.strip()
            if len(s)>240 or len(s)<8:
                continue
            for pat,kind in HEURISTIC_PATTERNS:
                if pat.search(s):
                    suggestions.append({"kind":kind,"text":s,"source":"current_user_message",
                                        "status":"suggestion_only","saved":False})
                    break
            if len(suggestions)>=3:
                break
        return suggestions

    def episode_preview(self,session_id:str)->dict:
        sid=check_id(session_id)
        rows=self.store.history(sid,30)
        if not rows:
            raise StoreError("Session has no stored messages")
        # Conservative extractive preview: never generates unseen autobiographical details.
        excerpts=[f"{r['role']}: {r['body'][:180]}" for r in rows if r["role"]=="user"][-10:]
        return {"session_id":sid,"digest":"\n".join(excerpts)[:1800],
                "message_count":len(rows),"status":"preview_only","confirmed":False}

    def save_episode(self,session_id:str,confirmed:bool)->dict:
        if confirmed is not True:
            raise StoreError("Owner confirmation required to store an episode")
        preview=self.episode_preview(session_id)
        record={"id":new_id(),"session_id":preview["session_id"],"digest":preview["digest"],
                "source_messages":preview["message_count"],"created_at":now()}
        with self.store.db() as c:
            c.execute("""INSERT INTO memory_episodes(id,session_id,digest,source_messages,created_at)
                VALUES(:id,:session_id,:digest,:source_messages,:created_at)""",record)
            self.store._audit(c,"episode_approved",record["id"],None,
                              json.dumps({"source_messages":record["source_messages"]}))
        return record

    def episodes(self,limit:int=25)->list[dict]:
        with self.store.db() as c:
            rows=c.execute("SELECT * FROM memory_episodes ORDER BY created_at DESC,id DESC LIMIT ?",
                           (max(1,min(int(limit),50)),)).fetchall()
        return [dict(r) for r in rows]

    def causal_link(self,cause_id:str,effect_id:str,evidence:str,confirmed:bool)->dict:
        if confirmed is not True:
            raise StoreError("Causal link requires explicit approval")
        a,b=check_id(cause_id),check_id(effect_id)
        if a==b:
            raise StoreError("Causal link requires distinct facts")
        evidence=clean(evidence,600)
        with self.store.db() as c:
            for mid in (a,b):
                row=c.execute("SELECT id FROM memories WHERE id=?",(mid,)).fetchone()
                if row is None:
                    raise StoreError("Fact not found")
            c.execute("""INSERT INTO causal_links(cause_id,effect_id,evidence_note,created_at) VALUES(?,?,?,?)
                ON CONFLICT(cause_id,effect_id) DO UPDATE SET evidence_note=excluded.evidence_note""",
                (a,b,evidence,now()))
            self.store._audit(c,"causal_link_approved",a,None,json.dumps({"effect_id":b}))
        return {"cause_id":a,"effect_id":b,"evidence_note":evidence,
                "claim":"user_approved_link_not_independently_verified"}

    def causal_links(self)->list[dict]:
        with self.store.db() as c:
            rows=c.execute("""SELECT cause_id,effect_id,evidence_note FROM causal_links
                ORDER BY created_at DESC LIMIT 100""").fetchall()
        return [dict(r) for r in rows]

    def _cached_vector(self,c,model:str,text:str)->list[float]|None:
        row=c.execute("SELECT vector_json FROM embedding_cache WHERE model=? AND text_sha256=?",
                      (model,_hash(text))).fetchone()
        return json.loads(row["vector_json"]) if row else None

    def _vector(self,model:str,text:str)->tuple[list[float],bool]:
        with self.store.db() as c:
            cached=self._cached_vector(c,model,text)
        if cached is not None:
            return cached,True
        vector=self.embed.encode(text)
        with self.store.db() as c:
            c.execute("INSERT OR IGNORE INTO embedding_cache(model,text_sha256,vector_json,created_at) VALUES(?,?,?,?)",
                      (model,_hash(text),json.dumps(vector),now()))
        return vector,False

    def index_memories(self,confirmed:bool,limit:int=12)->dict:
        if confirmed is not True or not self.permission("cloud_embeddings"):
            raise StoreError("Cloud embedding indexing requires explicit owner permission")
        if not self.embed.ready():
            raise StoreError("Cloud embedding configuration missing")
        limit=max(1,min(int(limit),12))
        trusted=self.cap.confirmed_memories(300)
        indexed=0;cache_hits=0
        for row in trusted[:limit]:
            # No batching without permission; caller gets capped cost exposure.
            vector,cached=self._vector(self.embed.model,row["body"])
            cache_hits+=int(cached)
            with self.store.db() as c:
                c.execute("""INSERT INTO memory_embeddings(memory_id,model,text_sha256,indexed_at)
                    VALUES(?,?,?,?) ON CONFLICT(memory_id,model)
                    DO UPDATE SET text_sha256=excluded.text_sha256,indexed_at=excluded.indexed_at""",
                    (row["id"],self.embed.model,_hash(row["body"]),now()))
            indexed+=1
        return {"indexed":indexed,"cache_hits":cache_hits,"model":self.embed.model,
                "background_calls":False,"may_incure_provider_charges":True}

    def vector_search(self,query:str,confirmed:bool,limit:int=6)->list[dict]:
        if confirmed is not True or not self.permission("cloud_embeddings"):
            raise StoreError("Cloud vector search requires owner permission")
        if not self.embed.ready():
            raise StoreError("Cloud embedding configuration missing")
        query=clean(query,250)
        vector,_=self._vector(self.embed.model,query)
        with self.store.db() as c:
            rows=c.execute("""SELECT m.id,m.kind,m.body,e.text_sha256,cache.vector_json
                FROM memory_embeddings e
                JOIN memories m ON m.id=e.memory_id
                LEFT JOIN memory_provenance p ON p.memory_id=m.id
                JOIN embedding_cache cache ON cache.model=e.model AND cache.text_sha256=e.text_sha256
                WHERE e.model=? AND COALESCE(p.status,'owner_confirmed')='owner_confirmed'
                LIMIT 300""",(self.embed.model,)).fetchall()
        scores=[]
        for r in rows:
            if _hash(r["body"])!=r["text_sha256"]:
                continue
            score=_cosine(vector,json.loads(r["vector_json"]))
            if score>0:
                scores.append({"id":r["id"],"kind":r["kind"],"body":r["body"],
                               "score":round(score,4),"method":"Cloud.ru embedding cosine"})
        scores.sort(key=lambda x:x["score"],reverse=True)
        return scores[:max(1,min(int(limit),20))]

    def evidence_search(self,query:str,limit:int=8,max_hops:int=2)->dict:
        """Offline lexical retrieval plus bounded traversal of approved memory links."""
        query=clean(query,250)
        limit=max(1,min(int(limit),8))
        max_hops=max(0,min(int(max_hops),3))
        hits=self.cap.memory_index(query,limit)
        roots=[r for r in hits if r.get("provenance")=="owner_confirmed"]
        if not roots:
            roots=self.cap.confirmed_memories(limit) if not hits else []
            # No fallback to unrelated memories for evidence when no lexical hit.
            if not hits:
                roots=[]
        with self.store.db() as c:
            edges=[dict(r) for r in c.execute("SELECT source_id,target_id,relation FROM memory_links LIMIT 300")]
            cause=[dict(r) for r in c.execute("SELECT cause_id,effect_id,evidence_note FROM causal_links LIMIT 100")]
        adj={}
        for r in edges:
            adj.setdefault(r["source_id"],[]).append((r["target_id"],r["relation"]))
            adj.setdefault(r["target_id"],[]).append((r["source_id"],r["relation"]))
        for r in cause:
            adj.setdefault(r["cause_id"],[]).append((r["effect_id"],"owner_labeled_cause"))
        queue=deque((r["id"],0) for r in roots)
        seen=set(); items=[]
        while queue and len(items)<limit:
            mid,depth=queue.popleft()
            if mid in seen:continue
            seen.add(mid)
            with self.store.db() as c:
                row=c.execute("""SELECT m.id,m.body,m.kind,
                    COALESCE(p.status,'owner_confirmed') AS provenance
                    FROM memories m LEFT JOIN memory_provenance p ON p.memory_id=m.id
                    WHERE m.id=?""",(mid,)).fetchone()
            if row and row["provenance"]=="owner_confirmed":
                items.append({**dict(row),"hop":depth,"origin":"approved_local_memory"})
                if depth<max_hops:
                    for next_id,_ in adj.get(mid,[])[:20]:
                        if next_id not in seen:
                            queue.append((next_id,depth+1))
        return {"query":query,"sources":items,"source_count":len(items),
                "scope":"local_approved_only","max_hops":max_hops,
                "causal_links":[c for c in cause if c["cause_id"] in seen and c["effect_id"] in seen]}

    def reasoning_plan(self,question:str)->dict:
        question=clean(question,6000)
        segments=[s.strip(" ?.;\n") for s in
                  re.split(r"[?;\n]+|\s+и\s+(?=почему|как|что|где|когда|зачем)",question,flags=re.I) if s.strip()]
        questions=segments[:5] or [question]
        tasks=[]
        for part in questions:
            evidence=self.evidence_search(part[:250],limit=4,max_hops=2)
            candidate=evidence["sources"]
            tasks.append({"question":part[:400],"source_ids":[x["id"] for x in candidate],
                          "evidence_count":len(candidate),
                          "confidence_label":"supported_by_owner_records" if candidate else "insufficient_evidence",
                          "notes":"Sources are user-confirmed notes, not independently verified external facts"})
        allids=list(dict.fromkeys(id for t in tasks for id in t["source_ids"]))
        return {"questions":tasks,"source_ids":allids[:8],
                "uncertainty":any(t["evidence_count"]==0 for t in tasks),
                "alternatives":["Additional evidence may change the conclusion"] if not allids else [],
                "reasoning_mode":"bounded_evidence_plan_not_independent_truth_verification"}

    def inspect_answer(self,answer:str,plan:dict)->dict:
        """No LLM, no hidden second inference. Warn, never claim to verify factual truth."""
        text=clean(answer,24000)
        uncertain=bool(plan.get("uncertainty"))
        ids=set(plan.get("source_ids",[]))
        return {"source_count":len(ids),"requires_source_review":uncertain,
                "confidence_label":"source_limited" if ids and not uncertain else "unverified",
                "detected_empty_answer":not bool(text.strip()),
                "proof_of_correctness":False,
                "note":"Local checks cannot prove factual correctness or logical validity"}
