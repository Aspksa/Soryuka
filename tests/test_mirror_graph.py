"""Soryuka Memory Mirror homepage regression tests.

The original Soryuka systems remain functional at /console. The public graph is
derived entirely from one local SQLite, never from pretend agent activity.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from core.mirror_graph import build_graph,activity
from core.soul_store import SoulStore
from core.soul_engine import SoryukaEngine
from core.server import AppHandler

class FakeCloud:
    def ready(self):return True
    def complete(self,messages,max_tokens=768):
        return "Ответ на запрос подтверждён.",{"total_tokens":10}

class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=SoulStore(Path(self.temp.name)/"Русская папка"/"memory.db")
        self.engine=SoryukaEngine(self.store,FakeCloud())
    def tearDown(self):self.temp.cleanup()
    def mem(self,txt,kind="fact"):return self.store.add_memory(kind,txt,True)

    def test_empty_graph_contains_only_real_persona_core(self):
        g=build_graph(self.store)
        self.assertIn("core:soryuka",{n["id"] for n in g["nodes"]})
        self.assertEqual(len([n for n in g["nodes"] if n["kind"]=="cluster"]),6)
        self.assertEqual(len([n for n in g["nodes"] if n["id"].startswith("mem:")]),0)
        self.assertTrue(any(n["id"]=="canon:soryuka.json" for n in g["nodes"]))
        self.assertEqual(g["stats"]["items"],0)
        self.assertEqual(g["source"],"local_sqlite")
        self.assertIn("real_data",g["mode"])

    def test_real_memories_and_approved_links_form_edges(self):
        a=self.mem("Чайная церемония")
        b=self.mem("Зелёный чай",kind="tradition")
        self.engine.v4.link(a["id"],b["id"],"related",True)
        g=build_graph(self.store)
        nodes={n["id"]:n for n in g["nodes"]}
        self.assertEqual(nodes["mem:"+a["id"]]["name"],"Чайная церемония")
        self.assertIn("mem:"+b["id"],nodes)
        self.assertTrue(any(e["relation"]=="related"
             and {e["source"],e["target"]}=={"mem:"+a["id"],"mem:"+b["id"]}
             for e in g["links"]))
        self.assertEqual(g["stats"]["by_category"]["memory"],2)

    def test_disputed_memory_is_visible_and_marked_not_verified(self):
        a=self.mem("Я был на Луне")
        self.engine.v4.set_provenance(a["id"],"owner_disputed","",True)
        g=build_graph(self.store)
        item=next(x for x in g["nodes"] if x["id"]=="mem:"+a["id"])
        self.assertEqual(item["status"],"owner_disputed")
        self.assertNotEqual(item["status"],"owner_confirmed")

    def test_conversation_and_growth_are_real_nodes(self):
        sid=self.store.ensure_session()
        self.store.save_exchange(sid,"Привет","Здравствуй","greeting")
        goal=self.store.propose_goal("логика","Разобрать задачу","3 решения")
        story=self.store.add_event("Ледяной дворец",fiction=True,confirmed=True)
        self.engine.v5.set_permission("background_local",True,True)
        task=self.engine.growth.enqueue("memory_review",True)
        g=build_graph(self.store);ids={n["id"] for n in g["nodes"]}
        self.assertIn("session:"+sid,ids)
        self.assertIn("goal:"+goal["id"],ids)
        self.assertIn("story:"+story["id"],ids)
        self.assertIn("task:"+task["id"],ids)
        self.assertEqual(len([x for x in ids if x.startswith("turn:")]),2)
        self.assertGreaterEqual(g["stats"]["items"],5)

    def test_deleted_memories_absent_after_refresh(self):
        a=self.mem("Подтверждённая запись")
        self.assertIn("mem:"+a["id"],{x["id"] for x in build_graph(self.store)["nodes"]})
        self.store.delete_memory(a["id"])
        self.assertNotIn("mem:"+a["id"],{x["id"] for x in build_graph(self.store)["nodes"]})

    def test_cursors_and_incremental_events(self):
        baseline=build_graph(self.store)["cursor"]
        self.assertEqual(activity(self.store,baseline["audit"],baseline["message"])["items"],[])
        memory=self.mem("Локальная память для графа")
        events=activity(self.store,baseline["audit"],baseline["message"])
        self.assertTrue(any(e["title"]=="Воспоминание сохранено"
               and "mem:"+memory["id"] in e["targets"] for e in events["items"]))
        self.assertFalse(events["simulated"])
        self.assertTrue(all("body" not in e for e in events["items"]))
        tail=activity(self.store,events["cursor"]["audit"],events["cursor"]["message"])
        self.assertEqual(tail["items"],[])

    def test_completed_chat_records_true_memory_access_without_private_text(self):
        a=self.mem("Люблю китайский чай")
        sid=self.store.ensure_session()
        before=build_graph(self.store)["cursor"]
        self.engine.chat("Какой чай я люблю?",session_id=sid,intent="greeting")
        items=activity(self.store,before["audit"],before["message"])["items"]
        reads=[x for x in items if x["kind"]=="read" and "mem:"+a["id"] in x["targets"]]
        self.assertEqual(len(reads),1)
        self.assertGreaterEqual(len([x for x in items if x["category"]=="dialog"]),2)
        dumped=json.dumps(items,ensure_ascii=False)
        self.assertNotIn("Какой чай я люблю?",dumped)
        self.assertNotIn("Люблю китайский чай",dumped)
        self.assertNotIn("Ответ на запрос подтверждён.",dumped)

    def test_bad_cursor_fails_closed(self):
        for bad in (-1,"not-a-number",2**54):
            with self.assertRaises(ValueError):
                activity(self.store,bad,0)

    def test_graph_respects_caps_and_no_unapproved_fake_memories(self):
        for i in range(5):self.mem("Факт номер "+str(i))
        g=build_graph(self.store)
        self.assertEqual(len([x for x in g["nodes"] if x["category"]=="memory" and x["kind"]=="fact"]),5)
        self.assertLessEqual(len(g["nodes"]),1200)

class MirrorApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.before=os.environ.get("SORYUKA_API_TOKEN")
        os.environ["SORYUKA_API_TOKEN"]="test-mirror-token"
        self.store=SoulStore(Path(self.temp.name)/"store.db")
        self.server=ThreadingHTTPServer(("127.0.0.1",0),AppHandler)
        self.server.store=self.store
        self.server.engine=SoryukaEngine(self.store,FakeCloud())
        self.server_thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.server_thread.start()
        self.url="http://127.0.0.1:"+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.server_thread.join(timeout=3)
        if self.before is None:os.environ.pop("SORYUKA_API_TOKEN",None)
        else:os.environ["SORYUKA_API_TOKEN"]=self.before
        self.temp.cleanup()
    def request(self,path,token=True):
        req=urllib.request.Request(self.url+path,headers={"X-Soryuka-Token":"test-mirror-token"} if token else {})
        try:
            with urllib.request.urlopen(req,timeout=5) as res:
                return res.status,json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code,json.loads(exc.read())

    def test_mirror_is_read_only_and_token_protected(self):
        status,_=self.request("/api/mirror/graph",token=False)
        self.assertEqual(status,403)
        status,graph=self.request("/api/mirror/graph")
        self.assertEqual(status,200)
        self.assertGreaterEqual(graph["stats"]["nodes"],7)
        self.assertEqual(graph["stats"]["items"],0)
        status,_=self.request("/api/mirror/activity?audit_after=0&message_after=0")
        self.assertEqual(status,200)

    def test_bad_cursor_rejected_by_api(self):
        status,_=self.request("/api/mirror/activity?audit_after=-10")
        self.assertEqual(status,400)

class MirrorUiTests(unittest.TestCase):
    def test_homepage_is_graph_only_legacy_ui_preserved(self):
        html=(ROOT/"ui"/"index.html").read_text(encoding="utf-8")
        old=(ROOT/"ui"/"console.html").read_text(encoding="utf-8")
        js=(ROOT/"ui"/"mirror-original.js").read_text(encoding="utf-8")
        self.assertIn('id="canvas-container"',html)
        self.assertIn('id="feedList"',html)
        self.assertIn('/mirror-original.js',html)
        self.assertIn('/vendor/three.module.js',html)
        self.assertIn('/vendor/d3-force-3d.min.js',html)
        self.assertNotIn('cdn.jsdelivr.net',html)
        self.assertNotIn('fonts.googleapis.com',html)
        self.assertNotIn('id="chat-form"',html)
        self.assertNotIn('id="goal-form"',html)
        self.assertIn('id="chat-form"',old)
        self.assertIn('id="goal-form"',old)
        self.assertIn('/app.js',old)
        for engine in ("THREE.FogExp2","THREE.WebGLRenderer","d3.forceSimulation",
                       "THREE.MeshStandardMaterial","THREE.SpriteMaterial",
                       "THREE.LineSegments","THREE.PointLight","THREE.CatmullRomCurve3",
                       "new WebSocket","/api/mirror/graph"):
            self.assertIn(engine,js)

if __name__=="__main__":
    unittest.main()
