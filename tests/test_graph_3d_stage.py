"""Real Soryuka 3D activity routing: no fake thought stream or leaked chat bodies."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.mirror_graph import activity,build_graph
from core.soul_store import SoulStore
from core.soul_engine import SoryukaEngine


class FakeCloud:
    def __init__(self):self.calls=[]
    def ready(self):return True
    def complete(self,messages,max_tokens=768):
        self.calls.append(messages)
        return "Ответ подтверждён.",{"total_tokens":10}


class Real3DRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=SoulStore(Path(self.tmp.name)/"тестовый каталог"/"soryuka.db")
        self.cloud=FakeCloud()
        self.engine=SoryukaEngine(self.store,self.cloud)
    def tearDown(self):self.tmp.cleanup()

    def test_request_and_response_phases_use_real_audit(self):
        record=self.store.add_memory("fact","Предпочтение китайского чая",True)
        before=build_graph(self.store)["cursor"]
        self.engine.chat("Какой чай я предпочитаю?",intent="technical")
        events=activity(self.store,before["audit"],before["message"])["items"]
        started=next(x for x in events if x["phase"]=="request_started")
        finished=next(x for x in events if x["phase"]=="response_saved")
        expected="mem:"+record["id"]
        self.assertEqual(started["context_kind"],"selected_for_prompt")
        self.assertEqual(finished["context_kind"],"selected_for_prompt")
        self.assertIn(expected,started["targets"])
        self.assertIn(expected,finished["targets"])
        self.assertEqual(started["source"],"sqlite_audit")
        self.assertEqual(finished["source"],"sqlite_audit")
        dumped=json.dumps(events,ensure_ascii=False)
        self.assertNotIn("Какой чай я предпочитаю?",dumped)
        self.assertNotIn("Предпочтение китайского чая",dumped)
        self.assertNotIn("Ответ подтверждён.",dumped)

    def test_real_memory_links_expose_both_verified_endpoints(self):
        left=self.store.add_memory("fact","Облако",True)
        right=self.store.add_memory("fact","Дождь",True)
        before=build_graph(self.store)["cursor"]
        self.engine.v4.link(left["id"],right["id"],"related",True)
        events=activity(self.store,before["audit"],before["message"])["items"]
        link=next(x for x in events if x["title"]=="Связь подтверждена")
        self.assertEqual(link["kind"],"link")
        self.assertEqual(set(link["targets"]),
                         {"mem:"+left["id"],"mem:"+right["id"]})
        self.assertFalse(any("body" in e for e in events))

    def test_audit_phases_keep_numeric_order_past_nine_records(self):
        for index in range(12):
            self.store.add_memory("fact","Проверенная запись "+str(index),True)
        before=build_graph(self.store)["cursor"]
        self.engine.chat("Какой факт известен?",intent="technical")
        items=activity(self.store,before["audit"],before["message"])["items"]
        start_idx=next(i for i,e in enumerate(items)
                       if e.get("phase")=="request_started")
        finish_idx=next(i for i,e in enumerate(items)
                        if e.get("phase")=="response_saved")
        self.assertLess(start_idx,finish_idx)

    def test_without_chat_there_is_no_invented_answer_route(self):
        self.store.add_memory("fact","Реальная запись",True)
        events=activity(self.store,0,0)["items"]
        self.assertTrue(events)
        self.assertTrue(all(e["phase"] is None for e in events
                            if e["source"]=="sqlite_audit"))
        self.assertTrue(all(e["context_kind"] is None for e in events
                            if e["source"]=="sqlite_audit"))


if __name__=="__main__":
    unittest.main()
