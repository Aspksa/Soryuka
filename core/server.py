"""Local-only JSON API for Soryuka. Default-deny, token-protected, no extra dependencies."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import sys

try:
    from .soul_engine import SoryukaEngine,CloudConfigError,CloudRequestError
    from .soul_store import SoulStore,StoreError
    from .growth_v5 import BackgroundLearningLoop
except ImportError:
    from soul_engine import SoryukaEngine,CloudConfigError,CloudRequestError
    from soul_store import SoulStore,StoreError
    from growth_v5 import BackgroundLearningLoop

MAX_BODY=32768


class AppHandler(BaseHTTPRequestHandler):
    server_version="SoryukaLocal/5.0"

    def log_message(self, format, *args):
        # Avoid logging private chat bodies or secret headers.
        if os.getenv("SORYUKA_VERBOSE")=="1":
            super().log_message(format,*args)

    def json_response(self, code:int, obj:dict|list):
        data=json.dumps(obj,ensure_ascii=False,separators=(",",":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Length",str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def allowed(self)->bool:
        provided=self.headers.get("X-Soryuka-Token","")
        expected=os.environ.get("SORYUKA_API_TOKEN","")
        if not expected or not hmac.compare_digest(provided,expected):
            self.json_response(403,{"error":"Forbidden"})
            return False
        return True

    def json_body(self)->dict:
        length=self.headers.get("Content-Length","")
        if not length.isascii() or not length.isdigit() or not 0<int(length)<=MAX_BODY:
            raise StoreError("Invalid request body size")
        if self.headers.get("Content-Type","").split(";")[0].strip().lower()!="application/json":
            raise StoreError("Content-Type must be application/json")
        try:
            payload=json.loads(self.rfile.read(int(length)).decode("utf-8"))
        except (ValueError,UnicodeDecodeError) as exc:
            raise StoreError("Invalid JSON") from exc
        if not isinstance(payload,dict):
            raise StoreError("Expected JSON object")
        return payload

    def handle_request(self,write:bool):
        if not self.allowed():
            return
        from urllib.parse import urlsplit,parse_qs
        parts=urlsplit(self.path)
        path=parts.path
        params=parse_qs(parts.query)
        try:
            if write:
                body=self.json_body()
                result=self.post(path,body)
            else:
                result=self.get(path,params)
            self.json_response(200,result)
        except StoreError as exc:
            self.json_response(400,{"error":str(exc)})
        except CloudConfigError as exc:
            self.json_response(503,{"error":str(exc)})
        except CloudRequestError as exc:
            self.json_response(502,{"error":str(exc)})
        except Exception:
            # Never reflect stack traces or secrets to browsers.
            self.json_response(500,{"error":"Internal server error"})

    def do_GET(self):
        self.handle_request(write=False)

    def do_POST(self):
        self.handle_request(write=True)

    def get(self,path:str,params:dict)->dict|list:
        store=self.server.store
        engine=self.server.engine
        if path=="/api/health":
            return {"ok":True,"version":"5.0","configured":engine.cloud.ready(),
                    "database":"ready","external_calls_automatic":False}
        if path=="/api/profile":
            return engine.profile()
        if path=="/api/sessions":
            return {"items":store.sessions()}
        if path=="/api/history":
            if not params.get("session_id"):
                raise StoreError("session_id required")
            return {"items":store.history(params["session_id"][0],30)}
        if path=="/api/memories":
            return {"items":store.list_memories(50)}
        if path=="/api/diary":
            return {"items":store.audit_log(50)}
        if path=="/api/timeline":
            return {"items":store.timeline(50)}
        if path=="/api/goals":
            return {"items":store.goals()}
        if path=="/api/metrics":
            return {"skills":store.skill_metrics(),"feedback":store.recent_feedback(10)}
        if path=="/api/recommendations":
            return {"items":store.recommend_goals()}
        if path=="/api/proposals":
            return {"items":store.proposals(),"preferences":store.preferences()}
        v4=engine.v4
        if path=="/api/memory/search":
            return {"items":v4.memory_index(params.get("q",[""])[0])}
        if path=="/api/memory/graph":
            return {"items":v4.links(params.get("id",[None])[0])}
        if path=="/api/memory/review":
            return {"items":v4.review_candidates(params.get("id",[""])[0])}
        if path=="/api/memory/provenance":
            return v4.provenance(params.get("id",[""])[0])
        if path=="/api/memory/chronology":
            return {"items":v4.chronology()}
        if path=="/api/relationship":
            return v4.relationship_context()
        if path=="/api/academy/map":
            return {"items":v4.skill_map()}
        if path=="/api/academy/assessments":
            return {"items":v4.assessments(params.get("goal_id",[None])[0])}
        if path=="/api/academy/strategies":
            return {"items":v4.strategies()}
        if path=="/api/academy/council":
            return {"items":v4.development_council()}
        if path=="/api/academy/diary":
            return {"items":v4.diary()}
        cognition=engine.v5
        growth=engine.growth
        if path=="/api/v5/permissions":
            return {"items":cognition.permissions(),"cloud_embeddings_ready":cognition.embed.ready()}
        if path=="/api/v5/episodes":
            return {"items":cognition.episodes()}
        if path=="/api/v5/episode/preview":
            return cognition.episode_preview(params.get("session_id",[""])[0])
        if path=="/api/v5/causal":
            return {"items":cognition.causal_links()}
        if path=="/api/v5/evidence":
            return cognition.evidence_search(params.get("q",[""])[0])
        if path=="/api/v5/reason":
            return cognition.reasoning_plan(params.get("q",[""])[0])
        if path=="/api/v5/growth/tasks":
            return {"items":growth.tasks()}
        if path=="/api/v5/growth/reviews":
            return {"items":growth.reviews()}
        if path=="/api/v5/growth/plan":
            return {"items":growth.plan()}
        if path=="/api/v5/growth/diary":
            return {"items":growth.evolution_diary()}
        if path=="/api/v5/skill/transfers":
            return {"items":growth.transfers()}
        raise StoreError("Unknown API route")

    def post(self,path:str,body:dict)->dict|list:
        store=self.server.store
        engine=self.server.engine
        if path=="/api/chat":
            return engine.chat(body.get("message",""),body.get("session_id"),body.get("intent"))
        if path=="/api/session":
            return {"session_id":store.ensure_session()}
        if path=="/api/session/delete":
            return {"deleted":store.delete_session(body.get("id",""))}
        if path=="/api/backup":
            return store.create_backup(body.get("confirmed",False))
        if path=="/api/memory":
            return store.add_memory(body.get("kind",""),body.get("text",""),body.get("confirmed",False))
        if path=="/api/memory/delete":
            return {"deleted":store.delete_memory(body.get("id",""))}
        if path=="/api/timeline/delete":
            return {"deleted":store.delete_event(body.get("id",""))}
        if path=="/api/feedback/delete":
            return {"deleted":store.delete_feedback(body.get("id",""))}
        if path=="/api/timeline":
            return store.add_event(body.get("text",""),body.get("fiction"),body.get("confirmed",False))
        if path=="/api/goal":
            return store.propose_goal(body.get("skill",""),body.get("title",""),body.get("criterion",""))
        if path=="/api/goal/action":
            return store.goal_action(body.get("id",""),body.get("action",""),body.get("evidence",""))
        if path=="/api/feedback":
            return store.add_feedback(body.get("skill",""),body.get("rating"),body.get("note",""))
        if path=="/api/proposal":
            return store.propose_change(body.get("field",""),body.get("value",""),body.get("reason",""))
        if path=="/api/proposal/action":
            return store.change_action(body.get("id",""),body.get("action",""))
        v4=engine.v4
        if path=="/api/memory/link":
            return v4.link(body.get("source_id",""),body.get("target_id",""),
                           body.get("relation",""),body.get("confirmed",False))
        if path=="/api/memory/unlink":
            return {"removed":v4.unlink(body.get("source_id",""),body.get("target_id",""),
                                         body.get("relation",""))}
        if path=="/api/memory/provenance":
            return v4.set_provenance(body.get("id",""),body.get("status",""),
                                     body.get("source_note",""),body.get("confirmed",False))
        if path=="/api/memory/merge":
            return v4.merge_memories(body.get("keep_id",""),body.get("remove_id",""),
                                      body.get("confirmed",False))
        if path=="/api/academy/assessment":
            return v4.assessment(body.get("goal_id",""),body.get("question",""),
                    body.get("reference",""),body.get("answer",""),body.get("confirmed",False),
                    body.get("review",""))
        if path=="/api/academy/strategy":
            return v4.propose_strategy(body.get("skill",""),body.get("name",""),
                    body.get("baseline"),body.get("trial"),body.get("evidence",""),
                    body.get("confirmed",False))
        if path=="/api/academy/strategy/action":
            return engine.growth.guard_strategy_change(body.get("id",""),body.get("action",""))
        cognition=engine.v5
        growth=engine.growth
        if path=="/api/v5/permissions":
            return cognition.set_permission(body.get("name",""),body.get("enabled"),
                                            body.get("confirmed",False))
        if path=="/api/v5/episode/save":
            return cognition.save_episode(body.get("session_id",""),body.get("confirmed",False))
        if path=="/api/v5/causal":
            return cognition.causal_link(body.get("cause_id",""),body.get("effect_id",""),
                                          body.get("evidence",""),body.get("confirmed",False))
        if path=="/api/v5/embeddings/index":
            return cognition.index_memories(body.get("confirmed",False),body.get("limit",5))
        if path=="/api/v5/embeddings/search":
            return {"items":cognition.vector_search(body.get("query",""),
                          body.get("confirmed",False),body.get("limit",6))}
        if path=="/api/v5/growth/enqueue":
            return growth.enqueue(body.get("kind",""),body.get("confirmed",False))
        if path=="/api/v5/growth/run":
            return growth.run_once()
        if path=="/api/v5/growth/review":
            return growth.review_action(body.get("id",""),body.get("action",""))
        if path=="/api/v5/strategy/decision":
            return growth.guard_strategy_change(body.get("id",""),body.get("action",""))
        if path=="/api/v5/strategy/compare":
            return growth.compare_strategy(body.get("id",""))
        if path=="/api/v5/skill/transfer":
            return growth.propose_transfer(body.get("from_skill",""),body.get("to_skill",""),
                                            body.get("strategy_id",""),body.get("rationale",""))
        if path=="/api/v5/skill/transfer/decision":
            return growth.transfer_action(body.get("id",""),body.get("action",""))
        raise StoreError("Unknown API route")


def serve(host="127.0.0.1",port:int|None=None,store:SoulStore|None=None,cloud=None):
    if host not in ("127.0.0.1","localhost"):
        raise ValueError("Public binding is forbidden")
    port=int(port if port is not None else os.environ.get("SORYUKA_API_PORT","53554"))
    if not os.environ.get("SORYUKA_API_TOKEN"):
        raise RuntimeError("SORYUKA_API_TOKEN must be provided by launcher")
    server=ThreadingHTTPServer(("127.0.0.1",port),AppHandler)
    server.store=store or SoulStore()
    server.engine=SoryukaEngine(server.store,cloud)
    server.daemon_threads=True
    server.growth_loop=BackgroundLearningLoop(server.engine.growth)
    server.growth_loop.start()
    print("Soryuka API listening at 127.0.0.1:"+str(server.server_port),flush=True)
    return server


if __name__=="__main__":
    service=None
    try:
        service=serve()
        service.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        if service is not None:
            service.growth_loop.stop()
            service.server_close()
