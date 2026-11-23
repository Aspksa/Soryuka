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
except ImportError:
    from soul_engine import SoryukaEngine,CloudConfigError,CloudRequestError
    from soul_store import SoulStore,StoreError

MAX_BODY=32768


class AppHandler(BaseHTTPRequestHandler):
    server_version="SoryukaLocal/3.0"

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
            return {"ok":True,"version":"3.0","configured":engine.cloud.ready(),
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
        if path=="/api/proposals":
            return {"items":store.proposals(),"preferences":store.preferences()}
        raise StoreError("Unknown API route")

    def post(self,path:str,body:dict)->dict|list:
        store=self.server.store
        engine=self.server.engine
        if path=="/api/chat":
            return engine.chat(body.get("message",""),body.get("session_id"),body.get("intent"))
        if path=="/api/session":
            return {"session_id":store.ensure_session()}
        if path=="/api/memory":
            return store.add_memory(body.get("kind",""),body.get("text",""),body.get("confirmed",False))
        if path=="/api/memory/delete":
            return {"deleted":store.delete_memory(body.get("id",""))}
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
    print("Soryuka API listening at 127.0.0.1:"+str(server.server_port),flush=True)
    return server


if __name__=="__main__":
    try:
        serve().serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
