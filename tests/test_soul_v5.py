"""Soryuka 5.0 regression tests — local opt-in reasoning, embeddings and safe growth."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import threading
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.soul_store import SoulStore,StoreError
from core.cognitive_v5 import CognitiveV5,EmbeddingService
from core.growth_v5 import GrowthV5,BackgroundLearningLoop
from core.soul_engine import SoryukaEngine
from core.server import AppHandler


class FakeEmbeddings:
    model="fake/embedding"
    def __init__(self):self.calls=[]
    def ready(self):return True
    def encode(self,text):
        self.calls.append(text)
        s=text.lower()
        return [float("чай" in s),float("кофе" in s),float("проект" in s),.01]


class FakeCloud:
    def __init__(self):self.calls=[]
    def ready(self):return True
    def complete(self,messages,max_tokens=768):
        self.calls.append(messages)
        return "Мой хозяин, я проверила данные.",{"total_tokens":15}


class CognitiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=SoulStore(Path(self.tmp.name)/"Русская папка"/"data.sqlite3")
        self.embed=FakeEmbeddings()
        self.cog=CognitiveV5(self.store,embed=self.embed)
        self.growth=GrowthV5(self.store,self.cog)
    def tearDown(self):self.tmp.cleanup()
    def mem(self,body,kind="fact"):
        return self.store.add_memory(kind,body,True)
    def goal(self,skill="логика"):
        g=self.store.propose_goal(skill,"Проверка","Эталонный ответ")
        self.store.goal_action(g["id"],"approve")
        return g

    def test_selection_is_exact_32(self):
        ids=self.cog.contract["selected_ids"]
        self.assertEqual(len(ids),32)
        self.assertEqual(len(set(ids)),32)
        self.assertEqual([x["id"] for x in self.cog.contract["features"]],ids)

    def test_permissions_all_off_by_default_and_explicit(self):
        self.assertEqual(self.cog.permissions(),
            {"background_local":False,"fact_suggestions":False,"cloud_embeddings":False})
        with self.assertRaises(StoreError):
            self.cog.set_permission("background_local",True,False)
        self.cog.set_permission("background_local",True,True)
        self.assertTrue(CognitiveV5(self.store).permission("background_local"))

    def test_fact_capture_is_suggestion_only(self):
        self.assertEqual(self.cog.suggested_facts("Я люблю чай"),[])
        self.cog.set_permission("fact_suggestions",True,True)
        s=self.cog.suggested_facts("Я люблю чай и классическую музыку.")
        self.assertEqual(s[0]["kind"],"preference")
        self.assertFalse(s[0]["saved"])
        self.assertEqual(self.store.list_memories(),[])

    def test_episodes_require_owner_and_exact_source(self):
        sid=self.store.ensure_session()
        self.store.save_exchange(sid,"Я изучаю китайский язык","Отлично","learning")
        preview=self.cog.episode_preview(sid)
        self.assertIn("Я изучаю китайский язык",preview["digest"])
        with self.assertRaises(StoreError):
            self.cog.save_episode(sid,False)
        saved=self.cog.save_episode(sid,True)
        self.assertEqual(saved["source_messages"],2)
        self.assertEqual(len(CognitiveV5(SoulStore(self.store.path)).episodes()),1)

    def test_episode_can_be_deleted_without_private_audit_copy(self):
        sid=self.store.ensure_session()
        self.store.save_exchange(sid,"Секретная фраза о прогулке","Поняла","greeting")
        episode=self.cog.save_episode(sid,True)
        with self.assertRaises(StoreError):
            self.cog.delete_episode(episode["id"],False)
        self.assertTrue(self.cog.delete_episode(episode["id"],True))
        self.assertEqual(self.cog.episodes(),[])
        audit=json.dumps(self.store.audit_log(),ensure_ascii=False)
        self.assertNotIn("Секретная фраза о прогулке",audit)

    def test_episode_deleting_session_removes_digest(self):
        sid=self.store.ensure_session()
        self.store.save_exchange(sid,"Урок","Ответ","learning")
        self.cog.save_episode(sid,True)
        self.store.delete_session(sid)
        self.assertEqual(self.cog.episodes(),[])

    def test_causal_links_are_never_inferred(self):
        a=self.mem("Я изучаю язык")
        b=self.mem("Я лучше знаю китайский")
        self.assertEqual(self.cog.causal_links(),[])
        with self.assertRaises(StoreError):
            self.cog.causal_link(a["id"],b["id"],"Источник",False)
        self.cog.causal_link(a["id"],b["id"],"Подтверждено мной",True)
        self.assertEqual(len(self.cog.causal_links()),1)

    def test_evidence_walk_bounded_and_ignores_disputed(self):
        a=self.mem("Люблю чай")
        b=self.mem("Чайные традиции")
        c=self.mem("Чай и семья")
        self.cog.cap.link(a["id"],b["id"],"related",True)
        self.cog.cap.link(b["id"],c["id"],"related",True)
        outcome=self.cog.evidence_search("чай",limit=8,max_hops=2)
        self.assertGreaterEqual(len(outcome["sources"]),2)
        self.cog.cap.set_provenance(c["id"],"owner_disputed","",True)
        outcome=self.cog.evidence_search("чай",limit=8)
        self.assertNotIn(c["id"],[r["id"] for r in outcome["sources"]])

    def test_reason_plan_and_answer_check_are_honest(self):
        result=self.cog.reasoning_plan("Какой автомобиль мне нравится?")
        self.assertTrue(result["uncertainty"])
        self.assertEqual(result["questions"][0]["confidence_label"],"insufficient_evidence")
        inspection=self.cog.inspect_answer("Не знаю",result)
        self.assertFalse(inspection["proof_of_correctness"])
        self.assertTrue(inspection["requires_source_review"])

    def test_embedding_opt_in_no_cloud_by_default(self):
        self.mem("Я люблю чай")
        with self.assertRaises(StoreError):
            self.cog.index_memories(True)
        with self.assertRaises(StoreError):
            self.cog.vector_search("чай",True)
        self.assertEqual(self.embed.calls,[])

    def test_embedding_cache_reuse_and_search(self):
        m=self.mem("Я люблю чай")
        self.cog.set_permission("cloud_embeddings",True,True)
        i=self.cog.index_memories(True)
        self.assertEqual(i["indexed"],1)
        first_calls=len(self.embed.calls)
        again=self.cog.index_memories(True)
        self.assertEqual(again["cache_hits"],1)
        self.assertEqual(len(self.embed.calls),first_calls)
        r=self.cog.vector_search("чай",True)
        self.assertEqual(r[0]["id"],m["id"])
        self.cog.vector_search("чай",True)
        self.assertEqual(len(self.embed.calls),first_calls+1)

    def test_offline_hybrid_uses_cached_vectors_only(self):
        m=self.mem("Я люблю чай")
        self.cog.set_permission("cloud_embeddings",True,True)
        self.cog.index_memories(True)
        before=len(self.embed.calls)
        local=self.cog.hybrid_search("чай")
        self.assertFalse(local["cloud_called"])
        self.assertEqual(len(self.embed.calls),before)
        self.assertEqual(local["items"][0]["id"],m["id"])
        hybrid=self.cog.hybrid_search("чай",use_cloud=True,confirmed=True)
        self.assertTrue(hybrid["cloud_called"])
        self.assertTrue(hybrid["items"])
        calls=len(self.embed.calls)
        again=self.cog.hybrid_search("чай")
        self.assertFalse(again["cloud_called"])
        self.assertEqual(len(self.embed.calls),calls)
        self.assertIsNotNone(again["items"][0]["vector_score"])

    def test_deleted_memory_does_not_leave_orphan_vector(self):
        m=self.mem("Я люблю зеленый чай")
        self.cog.set_permission("cloud_embeddings",True,True)
        self.cog.index_memories(True)
        self.store.delete_memory(m["id"])
        self.assertGreaterEqual(self.cog.prune_orphan_embeddings(),1)
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM embedding_cache").fetchone()[0],0)

    def test_embedding_disputed_memory_is_filtered(self):
        m=self.mem("Люблю чай")
        self.cog.set_permission("cloud_embeddings",True,True)
        self.cog.index_memories(True)
        self.cog.cap.set_provenance(m["id"],"owner_disputed","",True)
        self.assertEqual(self.cog.vector_search("чай",True),[])

    def test_embedding_invalid_schema_protects_data(self):
        client=EmbeddingService(key="fake",model="fake/embedding")
        class FakeResponse:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,*a):return b'{"data":[{"embedding":[1,"invalid"]}]}'
        with patch("core.cognitive_v5.urllib.request.urlopen",return_value=FakeResponse()):
            with self.assertRaises(StoreError):
                client.encode("Чай")

    def test_task_runs_are_disabled_without_permission(self):
        self.growth.enqueue("memory_review",True)
        result=self.growth.run_once()
        self.assertEqual(result["status"],"disabled")
        self.assertEqual(self.growth.tasks()[0]["status"],"queued")

    def test_background_queue_persists_and_resumes(self):
        self.growth.enqueue("growth_recommendations",True)
        self.cog.set_permission("background_local",True,True)
        with self.store.db() as c:
            c.execute("UPDATE growth_tasks SET status='running',lease_until='2000-01-01T00:00:00+00:00'")
        new=GrowthV5(SoulStore(self.store.path))
        self.assertEqual(new.recover(),1)
        self.assertEqual(new.run_once()["processed"],1)
        self.assertEqual(new.tasks()[0]["status"],"done")

    def test_schedule_deduplicates_every_15_minutes(self):
        self.cog.set_permission("background_local",True,True)
        r=self.growth.schedule_cycle()
        self.assertEqual(r["enqueued"],3)
        r2=self.growth.schedule_cycle()
        self.assertEqual(r2["enqueued"],0)
        self.assertEqual(len(self.growth.tasks()),3)

    def test_memory_review_proposals_do_not_merge(self):
        self.mem("Я люблю зеленый чай")
        self.mem("Мне нравится зеленый чай")
        self.cog.set_permission("background_local",True,True)
        self.growth.enqueue("memory_review",True)
        self.growth.run_once()
        self.assertEqual(len(self.store.list_memories()),2)
        self.assertTrue(any(x["kind"]=="memory_review" for x in self.growth.reviews()))
        row=self.growth.reviews()[0]
        self.assertFalse(self.growth.review_action(row["id"],"accept")["applied_changes"])
        self.assertEqual(len(self.store.list_memories()),2)

    def test_exercises_created_from_actual_failed_checks(self):
        goal=self.goal()
        self.cog.cap.assessment(goal["id"],"Два плюс два?","четыре","пять",True)
        self.cog.set_permission("background_local",True,True)
        self.growth.enqueue("learning_exercises",True)
        self.growth.run_once()
        review=self.growth.reviews()[0]
        self.assertEqual(review["candidate"]["source_assessment_id"],
                         self.cog.cap.assessments()[0]["id"])
        self.assertEqual(len(self.cog.cap.assessments()),1)

    def test_no_background_embeddings_or_cloud_calls(self):
        self.mem("Я люблю чай")
        self.cog.set_permission("background_local",True,True)
        self.growth.schedule_cycle()
        self.assertEqual(self.growth.run_once()["network_calls"],0)
        self.assertEqual(self.embed.calls,[])

    def test_growth_plan_read_only(self):
        self.mem("Встреча с другом")
        before=len(self.growth.tasks())
        result=self.growth.plan()
        self.assertTrue(result)
        self.assertEqual(len(self.growth.tasks()),before)

    def test_strategy_requires_better_results(self):
        a=self.cog.cap.propose_strategy("логика","Плохой метод",90,50,"10 тестов",True)
        with self.assertRaises(StoreError):
            self.growth.guard_strategy_change(a["id"],"approve")
        b=self.cog.cap.propose_strategy("языки","Лучший метод",50,80,"10 тестов",True)
        self.assertEqual(self.growth.guard_strategy_change(b["id"],"approve")["status"],"approved")
        self.assertEqual(self.growth.guard_strategy_change(b["id"],"rollback")["status"],"rolled_back")

    def test_transfer_requires_approved_source(self):
        a=self.cog.cap.propose_strategy("логика","Переносим",45,70,"Тесты",True)
        with self.assertRaises(StoreError):
            self.growth.propose_transfer("логика","общение",a["id"],"Хочу проверить")
        self.growth.guard_strategy_change(a["id"],"approve")
        t=self.growth.propose_transfer("логика","общение",a["id"],"Проверить новый контекст")
        self.assertEqual(t["status"],"pending")
        x=self.growth.transfer_action(t["id"],"approve")
        self.assertFalse(x["applied_to_model"])
        self.assertEqual(self.growth.transfer_action(t["id"],"revoke")["status"],"revoked")

    def test_chat_only_proposes_facts_without_writing_memory(self):
        self.cog.set_permission("fact_suggestions",True,True)
        client=FakeCloud()
        engine=SoryukaEngine(self.store,client)
        result=engine.chat("Я люблю чай",intent="greeting")
        self.assertTrue(result["memory_suggestions"])
        self.assertEqual(self.store.list_memories(),[])
        self.assertIn("reasoning_review",result)
        self.assertEqual(engine.profile()["v5_selected_features"],32)

    def test_chat_includes_bounded_approved_multi_hop_source_text(self):
        one=self.mem("Думаю о чайной церемонии")
        two=self.mem("Традиция пить зелёный чай")
        self.cog.cap.link(one["id"],two["id"],"related",True)
        cloud=FakeCloud()
        engine=SoryukaEngine(self.store,cloud)
        engine.chat("чайной",intent="greeting")
        sys_prompt=cloud.calls[-1][0]["content"]
        self.assertIn("evidence_sources",sys_prompt)
        self.assertIn("Традиция пить зелёный чай",sys_prompt)
        self.cog.cap.set_provenance(two["id"],"owner_disputed","",True)
        engine.chat("чайной",intent="greeting")
        sys_prompt=cloud.calls[-1][0]["content"]
        self.assertNotIn("Традиция пить зелёный чай",sys_prompt)

    def test_canon_files_never_mutate(self):
        path=ROOT/"character"/"soryuka.json"
        before=path.read_bytes()
        self.cog.set_permission("background_local",True,True)
        self.growth.schedule_cycle()
        self.growth.run_once()
        self.assertEqual(before,path.read_bytes())

    def test_background_loop_can_stop(self):
        loop=BackgroundLearningLoop(self.growth,interval=20)
        loop.start()
        loop.stop()
        self.assertTrue(loop.stop_event.is_set())


class Api5Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=os.environ.get("SORYUKA_API_TOKEN")
        os.environ["SORYUKA_API_TOKEN"]="test-v5-token"
        self.store=SoulStore(Path(self.temp.name)/"test.db")
        self.server=ThreadingHTTPServer(("127.0.0.1",0),AppHandler)
        self.server.store=self.store
        self.server.engine=SoryukaEngine(self.store,FakeCloud())
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base=f"http://127.0.0.1:{self.server.server_port}"
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        if self.old is None:os.environ.pop("SORYUKA_API_TOKEN",None)
        else:os.environ["SORYUKA_API_TOKEN"]=self.old
        self.temp.cleanup()
    def req(self,path,body=None,token=True):
        data=json.dumps(body,ensure_ascii=False).encode("utf-8") if body is not None else None
        headers={"X-Soryuka-Token":"test-v5-token"} if token else {}
        if data is not None:headers["Content-Type"]="application/json"
        url=self.base+path
        request=urllib.request.Request(url,headers=headers,data=data)
        try:
            with urllib.request.urlopen(request,timeout=5) as r:
                return r.status,json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code,json.loads(e.read())

    def test_api_v5_protected_and_off_by_default(self):
        status,_=self.req("/api/v5/permissions",token=False)
        self.assertEqual(status,403)
        code,p=self.req("/api/v5/permissions")
        self.assertEqual(code,200)
        self.assertFalse(p["items"]["background_local"])

    def test_http_local_reason_and_permissions(self):
        code,_=self.req("/api/v5/permissions",{"name":"background_local","enabled":True})
        self.assertEqual(code,400)
        code,p=self.req("/api/v5/permissions",{"name":"background_local","enabled":True,"confirmed":True})
        self.assertEqual(code,200)
        code,r=self.req("/api/v5/growth/run",{})
        self.assertEqual(code,200)
        self.assertEqual(r["network_calls"],0)

    def test_api_episode_and_evidence(self):
        _,s=self.req("/api/session",{})
        self.req("/api/chat",{"session_id":s["session_id"],"message":"Привет","intent":"greeting"})
        code,preview=self.req("/api/v5/episode/preview?session_id="+s["session_id"])
        self.assertEqual(code,200)
        self.assertIn("Привет",preview["digest"])
        code,_=self.req("/api/v5/episode/save",{"session_id":s["session_id"]})
        self.assertEqual(code,400)
        code,_=self.req("/api/v5/episode/save",{"session_id":s["session_id"],"confirmed":True})
        self.assertEqual(code,200)

    def test_api_cloud_embeds_need_permission(self):
        code,_=self.req("/api/v5/embeddings/index",{"confirmed":True})
        self.assertEqual(code,400)


class V5UIContractTests(unittest.TestCase):
    def test_all_ui_controls_exist_and_js_matches_ts(self):
        page=(ROOT/"ui"/"index.html").read_text(encoding="utf-8")
        js=(ROOT/"ui"/"app.js").read_text(encoding="utf-8")
        self.assertEqual(js,(ROOT/"ui"/"app.ts").read_text(encoding="utf-8"))
        for name in ["v5-background","v5-save-permissions","v5-capture","v5-episode-preview",
                     "v5-index-embeddings","v5-run-growth","v5-evidence","v5-plan","v5-transfers"]:
            self.assertIn('id="'+name+'"',page)
        self.assertIn("setupV5();",js)


if __name__=="__main__":
    unittest.main()
