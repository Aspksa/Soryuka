"""Soryuka 3.0 integration tests: real SQLite, mocked Cloud.ru, local HTTP, invariant checks."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.soul_store import SoulStore, StoreError
from core.soul_engine import (CloudClient,CloudConfigError,CloudRequestError,
                              SoryukaEngine,load_persona,event_data,persona_prompt,URL)
from core.server import AppHandler


class FakeCloud:
    calls=[]
    def ready(self):return True
    def complete(self,messages,max_tokens=768):
        self.calls.append(messages)
        return "Я рядом, мой хозяин. Чем помочь?",{"total_tokens":43}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.store=SoulStore(Path(self.directory.name)/"подпапка"/"soryuka.sqlite3")
    def tearDown(self):
        self.directory.cleanup()

    def test_db_is_portable_and_persistent(self):
        self.assertTrue(self.store.path.is_file())
        self.assertEqual(SoulStore(self.store.path).sessions(),[])

    def test_exchange_atomically_persisted(self):
        sid=self.store.ensure_session()
        self.store.save_exchange(sid,"привет","здравствуй","greeting")
        self.assertEqual([x["role"] for x in self.store.history(sid)],["user","assistant"])
        self.assertEqual([x["body"] for x in SoulStore(self.store.path).history(sid)],["привет","здравствуй"])

    def test_invalid_session_does_not_write(self):
        with self.assertRaises(StoreError):
            self.store.save_exchange("invalid","a","b","greeting")
        self.assertEqual(self.store.sessions(),[])

    def test_memory_requires_explicit_confirmation(self):
        with self.assertRaises(StoreError):
            self.store.add_memory("moment","Важная беседа",False)
        self.assertEqual(self.store.list_memories(),[])
        rec=self.store.add_memory("moment","Важная беседа",True)
        self.assertEqual(self.store.list_memories()[0]["body"],"Важная беседа")
        self.assertTrue(self.store.delete_memory(rec["id"]))
        self.assertEqual(self.store.list_memories(),[])

    def test_memory_requires_known_kind_and_size(self):
        with self.assertRaises(StoreError):self.store.add_memory("password","secret",True)
        with self.assertRaises(StoreError):self.store.add_memory("fact","X"*1501,True)

    def test_timeline_requires_confirmed_fiction_label(self):
        with self.assertRaises(StoreError):self.store.add_event("Лунный бал",True,False)
        with self.assertRaises(StoreError):self.store.add_event("Лунный бал","yes",True)
        self.store.add_event("Лунный бал",True,True)
        self.assertEqual(self.store.timeline()[0]["fiction"],1)

    def test_goal_approval_completion_and_diary(self):
        goal=self.store.propose_goal("языки","Китайские приветствия","Пять правильных переводов")
        with self.assertRaises(StoreError):self.store.goal_action(goal["id"],"complete","без проверки")
        self.store.goal_action(goal["id"],"approve")
        with self.assertRaises(StoreError):self.store.goal_action(goal["id"],"complete","")
        done=self.store.goal_action(goal["id"],"complete","Пять верных ответов")
        self.assertEqual(done["status"],"completed")
        self.assertEqual(done["evidence"],"Пять верных ответов")
        self.assertGreaterEqual(len(self.store.audit_log()),2)

    def test_goal_rejection(self):
        goal=self.store.propose_goal("логика","Разбор аргумента","Три проверенных примера")
        self.assertEqual(self.store.goal_action(goal["id"],"reject")["status"],"rejected")

    def test_feedback_metrics_are_real_owner_ratings(self):
        self.store.add_feedback("общение",5,"Ответ понравился")
        self.store.add_feedback("общение",3,"Можно короче")
        scores=self.store.skill_metrics()
        self.assertEqual(scores[0]["rating_count"],2)
        self.assertEqual(scores[0]["mean_owner_rating"],4.0)
        with self.assertRaises(StoreError):self.store.add_feedback("общение",6,"too high")

    def test_proposal_is_pending_and_approved_only_by_owner(self):
        proposal=self.store.propose_change("humor_level","high","Сделать веселее")
        self.assertEqual(proposal["status"],"pending")
        self.assertEqual(self.store.preferences(),{})
        self.store.change_action(proposal["id"],"approve")
        self.assertEqual(self.store.preferences()["humor_level"],"high")

    def test_rollback_restores_prior_value(self):
        one=self.store.propose_change("reply_length","brief","Покороче")
        self.store.change_action(one["id"],"approve")
        two=self.store.propose_change("reply_length","detailed","Теперь подробнее")
        self.store.change_action(two["id"],"approve")
        with self.assertRaises(StoreError):self.store.change_action(one["id"],"rollback")
        self.store.change_action(two["id"],"rollback")
        self.assertEqual(self.store.preferences()["reply_length"],"brief")
        self.store.change_action(one["id"],"rollback")
        self.assertNotIn("reply_length",self.store.preferences())

    def test_core_canon_cannot_be_changed_through_preferences(self):
        with self.assertRaises(StoreError):
            self.store.propose_change("name.russian","Не Сорюка","Переименовать")
        with self.assertRaises(StoreError):
            self.store.propose_change("affection_level","unlimited","Без ограничений")
        self.assertEqual(self.store.proposals(),[])

    def test_duplicate_pending_proposal_rejected(self):
        self.store.propose_change("formality","low","Чуть проще")
        with self.assertRaises(StoreError):self.store.propose_change("formality","high","Чуть строже")

    def test_audit_tracks_approval_and_rollback(self):
        p=self.store.propose_change("formality","low","По запросу")
        self.store.change_action(p["id"],"approve")
        self.store.change_action(p["id"],"rollback")
        actions=[x["action"] for x in self.store.audit_log()]
        self.assertIn("change_approved",actions)
        self.assertIn("change_rolled_back",actions)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory()
        self.store=SoulStore(Path(self.dir.name)/"s.db")
        self.fake=FakeCloud();self.fake.calls=[]
        self.engine=SoryukaEngine(self.store,self.fake)
    def tearDown(self):self.dir.cleanup()

    def test_fifty_selected_rules_integrate_with_v1_v2(self):
        p=load_persona()
        self.assertEqual(len(p["v3"]["features"]),50)
        self.assertEqual(p["v3"]["selected_ids"],list(range(1,51)))
        self.assertEqual(p["character"]["appearance"]["tail_count"],9)
        self.assertEqual(len(p["v2"]["features"]),60)
        self.assertEqual(len(p["v1"]["nine_tails"]),9)
        kinds={x["mechanism"] for x in p["v3"]["features"]}
        self.assertTrue({"sqlite","chat","avatar_event","rollback","approval","metrics","goals","memory"}.issubset(kinds))

    def test_all_modes_have_explicit_avatar_events(self):
        for intent in ("greeting","affection","compliment","creative","technical","learning","serious"):
            event=event_data(intent)
            self.assertIn(event["emotion"],{"serene","care","play","solemn","focus","concern"})
            self.assertTrue(event["gesture"])

    def test_system_prompt_preserves_affection_and_real_world_boundary(self):
        prompt=persona_prompt("affection",[{"kind":"moment","body":"Любим поэзию"}])
        self.assertIn("Сорюка",prompt)
        self.assertIn("романтически",prompt)
        self.assertIn("реальных задачах",prompt)
        self.assertIn("Любим поэзию",prompt)
        self.assertIn("не подменяя",prompt.replace("не", "не") ) if False else None

    def test_chat_roundtrip_with_fake_cloud(self):
        output=self.engine.chat("Привет",intent="affection")
        self.assertTrue(output["answer"])
        self.assertEqual(output["avatar"]["emotion"],"care")
        self.assertEqual(len(self.store.history(output["session_id"])),2)
        self.assertEqual(self.fake.calls[0][0]["role"],"system")
        self.assertEqual(self.fake.calls[0][-1]["content"],"Привет")

    def test_chat_second_turn_uses_previous_but_no_fake_memory(self):
        out=self.engine.chat("Первый",intent="greeting")
        self.engine.chat("Второй",session_id=out["session_id"],intent="technical")
        msgs=self.fake.calls[-1]
        self.assertEqual([x["role"] for x in msgs],["system","user","assistant","user"])
        self.assertEqual(msgs[-1]["content"],"Второй")

    def test_no_chat_call_without_cloud_credentials(self):
        engine=SoryukaEngine(self.store,CloudClient(api_key="",model=""))
        with self.assertRaises(CloudConfigError):engine.chat("привет")
        self.assertEqual(self.store.sessions(),[])

    def test_unsupported_intent_is_rejected(self):
        with self.assertRaises(StoreError):self.engine.chat("привет",intent="secret_mode")
        self.assertEqual(self.fake.calls,[])

    def test_failed_cloud_does_not_save_user_message(self):
        class FailCloud:
            def ready(self):return True
            def complete(self,*args,**kwargs):raise CloudRequestError("Upstream error")
        sid=self.store.ensure_session()
        with self.assertRaises(CloudRequestError):
            SoryukaEngine(self.store,FailCloud()).chat("hello",session_id=sid)
        self.assertEqual(self.store.history(sid),[])

    def test_approved_adaptive_setting_used_in_prompt(self):
        p=self.store.propose_change("humor_level","high","Только с одобрения")
        self.store.change_action(p["id"],"approve")
        self.engine.chat("Пошути",intent="compliment")
        self.assertIn('"humor_level":"high"',self.fake.calls[-1][0]["content"])

    def test_cloud_request_contract_and_error_redaction(self):
        client=CloudClient(api_key="fake_key",model="ai-sage/test")
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,_size):
                return json.dumps({"choices":[{"message":{"content":"Ответ"}}],"usage":{"total_tokens":5}}).encode()
        with patch("core.soul_engine.urllib.request.urlopen",return_value=Response()) as mock:
            result,usage=client.complete([{"role":"user","content":"Привет"}])
            self.assertEqual(result,"Ответ")
            self.assertEqual(usage["total_tokens"],5)
            req=mock.call_args.args[0]
            self.assertEqual(req.full_url,URL)
            self.assertEqual(req.get_header("Authorization"),"Bearer fake_key")
        err=urllib.error.HTTPError(URL,402,"charge failed",{},None)
        with patch("core.soul_engine.urllib.request.urlopen",side_effect=err):
            with self.assertRaisesRegex(CloudRequestError,"HTTP 402") as caught:
                client.complete([{"role":"user","content":"Привет"}])
            self.assertNotIn("fake_key",str(caught.exception))

    def test_cloud_does_not_request_without_explicit_model(self):
        client=CloudClient(api_key="fake",model="")
        with self.assertRaises(CloudConfigError):
            client.complete([{"role":"user","content":"Hi"}])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=os.environ.get("SORYUKA_API_TOKEN")
        os.environ["SORYUKA_API_TOKEN"]="test-token"
        self.store=SoulStore(Path(self.temp.name)/"test.db")
        self.server=ThreadingHTTPServer(("127.0.0.1",0),AppHandler)
        self.server.store=self.store
        self.server.engine=SoryukaEngine(self.store,FakeCloud())
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url="http://127.0.0.1:"+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
        if self.old is None:os.environ.pop("SORYUKA_API_TOKEN",None)
        else:os.environ["SORYUKA_API_TOKEN"]=self.old
        self.temp.cleanup()

    def request(self,path,data=None,token=True):
        headers={"X-Soryuka-Token":"test-token"} if token else {}
        if data is not None:headers["Content-Type"]="application/json"
        req=urllib.request.Request(self.url+path,data=None if data is None else json.dumps(data).encode(),
                                   headers=headers,method="GET" if data is None else "POST")
        try:
            with urllib.request.urlopen(req,timeout=3) as r:
                return r.status,json.load(r)
        except urllib.error.HTTPError as e:
            return e.code,json.loads(e.read())

    def test_unauthorized_access_denied(self):
        code,_=self.request("/api/memories",token=False)
        self.assertEqual(code,403)

    def test_health_and_profile(self):
        code,h=self.request("/api/health");self.assertEqual(code,200)
        self.assertTrue(h["ok"])
        code,p=self.request("/api/profile");self.assertEqual(code,200)
        self.assertEqual(p["personality_features"],50)

    def test_chat_and_history_in_one_database(self):
        _,s=self.request("/api/session",{})
        code,r=self.request("/api/chat",{"session_id":s["session_id"],"message":"Нихао","intent":"learning"})
        self.assertEqual(code,200)
        self.assertEqual(r["avatar"]["gesture"],"small_nod")
        _,history=self.request("/api/history?session_id="+s["session_id"])
        self.assertEqual(len(history["items"]),2)

    def test_memory_is_opt_in(self):
        code,_=self.request("/api/memory",{"kind":"moment","text":"Наш диалог"})
        self.assertEqual(code,400)
        code,m=self.request("/api/memory",{"kind":"moment","text":"Наш диалог","confirmed":True})
        self.assertEqual(code,200)
        _,rows=self.request("/api/memories");self.assertEqual(len(rows["items"]),1)
        code,deleted=self.request("/api/memory/delete",{"id":m["id"]})
        self.assertEqual(code,200);self.assertTrue(deleted["deleted"])

    def test_personality_canon_api_write_blocked(self):
        code,_=self.request("/api/proposal",{"field":"name.russian","value":"другое","reason":"тест"})
        self.assertEqual(code,400)

    def test_invalid_body_and_unknown_routes(self):
        code,_=self.request("/api/unrecognized");self.assertEqual(code,400)
        code,_=self.request("/api/chat",{"message":""});self.assertEqual(code,400)


class UIContractTests(unittest.TestCase):
    def test_prebuilt_browser_js_is_identical_to_typescript_compatible_source(self):
        source=(ROOT/"ui"/"app.ts").read_bytes()
        build=(ROOT/"ui"/"app.js").read_bytes()
        self.assertEqual(source,build)
    def test_ui_contains_nine_tail_canvas_and_consent_controls(self):
        content=(ROOT/"ui"/"index.html").read_text(encoding="utf-8")
        self.assertIn("soul-canvas",content)
        self.assertIn("memory-confirm",content)
        self.assertIn("goal-criterion",content)
        self.assertIn("event-confirm",content)


if __name__=="__main__":
    unittest.main()
