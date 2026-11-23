"""Regression tests for Soryuka 4.0: 25 selected features, local memory and academy."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.soul_store import SoulStore,StoreError
from core.capabilities_v4 import SoulCapabilities,SELECTED
from core.soul_engine import SoryukaEngine
from core.server import AppHandler


class FakeCloud:
    def __init__(self):self.calls=[]
    def ready(self):return True
    def complete(self,messages,max_tokens=768):
        self.calls.append(messages)
        return "С возвращением, мой хозяин.",{"total_tokens":17}


class LocalCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=SoulStore(Path(self.temp.name)/"Диск с пробелами"/"soryuka.sqlite3")
        self.v4=SoulCapabilities(self.store)
    def tearDown(self):self.temp.cleanup()

    def memory(self,body,kind="fact"):
        return self.store.add_memory(kind,body,True)

    def approved_goal(self,skill="логика"):
        g=self.store.propose_goal(skill,"Проверить пример","Ответить на вопрос по эталону")
        self.store.goal_action(g["id"],"approve")
        return g

    def test_exact_selected_25_without_duplicates(self):
        v4=self.v4.config
        self.assertEqual(len(v4["feature_contracts"]),25)
        self.assertEqual(v4["selected_ids"],SELECTED)
        self.assertEqual([x["id"] for x in v4["feature_contracts"]],SELECTED)
        self.assertTrue(v4["principles"]["only_one_sqlite_db"])

    def test_preserves_original_v3_data_and_existing_sqlite(self):
        a=self.memory("Я люблю зелёный чай")
        saved=SoulStore(self.store.path)
        v4again=SoulCapabilities(saved)
        self.assertEqual(v4again.memory_index("чай")[0]["id"],a["id"])
        with saved.db() as db:
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        self.assertIn("messages",tables)
        self.assertIn("memory_links",tables)
        self.assertIn("academy_assessments",tables)

    def test_search_local_multilingual(self):
        a=self.memory("Я люблю чай")
        b=self.memory("Китайские стихи на праздник")
        result=self.v4.memory_index("чай")
        self.assertEqual(result[0]["id"],a["id"])
        self.assertNotIn(b["id"],[r["id"] for r in result])
        self.assertEqual(result[0]["match_method"],"local_token_similarity")
        self.assertEqual(self.v4.memory_index("и"),[])

    def test_search_respects_invalid_input_and_limit(self):
        with self.assertRaises(StoreError):self.v4.memory_index("")
        with self.assertRaises(StoreError):self.v4.memory_index("a"*251)
        self.assertEqual(self.v4.memory_index("несуществующее"),[])

    def test_relationship_memory_only_confirmed(self):
        self.memory("Первый совместный праздник",kind="moment")
        self.memory("Чайный ритуал вечером",kind="tradition")
        self.memory("Факт из книги",kind="fact")
        self.store.add_event("Императорский лунный бал",fiction=True,confirmed=True)
        self.store.add_event("Реальная встреча",fiction=False,confirmed=True)
        ctx=self.v4.relationship_context()
        self.assertEqual(len(ctx["approved_memories"]),2)
        self.assertEqual(len(ctx["fictional_events"]),1)
        self.assertIn("Императорский",ctx["fictional_events"][0]["body"])

    def test_link_requires_owner_confirmation(self):
        a=self.memory("Я люблю чай")
        b=self.memory("Чайная церемония")
        with self.assertRaises(StoreError):
            self.v4.link(a["id"],b["id"],"related",False)
        self.assertEqual(self.v4.links(),[])
        item=self.v4.link(a["id"],b["id"],"related",True)
        self.assertEqual(item["relation"],"related")
        self.assertEqual(len(self.v4.links()),1)

    def test_symmetrical_links_and_repeated_link(self):
        a=self.memory("Лисий праздник")
        b=self.memory("Императорский праздник")
        self.v4.link(a["id"],b["id"],"same_topic",True)
        self.v4.link(b["id"],a["id"],"same_topic",True)
        self.assertEqual(len(self.v4.links()),1)
        self.assertTrue(self.v4.unlink(b["id"],a["id"],"same_topic"))
        self.assertEqual(self.v4.links(),[])

    def test_deleting_memory_cascades_links_and_provenance(self):
        a=self.memory("Лист бумаги")
        b=self.memory("Заметка на бумаге")
        self.v4.link(a["id"],b["id"],"supports",True)
        self.v4.set_provenance(a["id"],"owner_disputed","",True)
        self.store.delete_memory(a["id"])
        self.assertEqual(self.v4.links(),[])
        with self.store.db() as db:
            n=db.execute("SELECT COUNT(*) FROM memory_provenance").fetchone()[0]
            self.assertEqual(n,0)

    def test_invalid_links_rejected(self):
        a=self.memory("A")
        with self.assertRaises(StoreError):self.v4.link(a["id"],a["id"],"related",True)
        with self.assertRaises(StoreError):self.v4.link(a["id"],"invalid","related",True)
        with self.assertRaises(StoreError):self.v4.link(a["id"],a["id"],"unknown",True)

    def test_candidate_duplicate_review_is_proposal_only(self):
        a=self.memory("Я люблю зелёный чай")
        b=self.memory("Мне нравится зелёный чай")
        r=self.v4.review_candidates(a["id"])
        self.assertTrue(any(x["memory_id"]==b["id"] and x["kind"]=="possible_duplicate" for x in r))
        self.assertEqual(len(self.store.list_memories()),2)

    def test_contradictions_are_not_automatically_resolved(self):
        a=self.memory("Я люблю чай")
        b=self.memory("Я не люблю чай")
        r=self.v4.review_candidates(a["id"])
        self.assertTrue(any(x["memory_id"]==b["id"] and x["kind"]=="possible_conflict" for x in r))
        with self.assertRaises(StoreError):self.v4.merge_memories(a["id"],b["id"],True)
        self.assertEqual(len(self.store.list_memories()),2)

    def test_duplicate_merge_explicit_permission_and_no_private_audit_text(self):
        a=self.memory("Я люблю зеленый чай")
        b=self.memory("Мне нравится зеленый чай")
        with self.assertRaises(StoreError):self.v4.merge_memories(a["id"],b["id"],False)
        result=self.v4.merge_memories(a["id"],b["id"],True)
        self.assertEqual(result["kept_id"],a["id"])
        self.assertEqual(len(self.store.list_memories()),1)
        audit=json.dumps(self.store.audit_log(),ensure_ascii=False)
        self.assertNotIn("Мне нравится зеленый чай",audit)

    def test_provenance_is_a_label_not_verified_truth(self):
        a=self.memory("Сорюка любит стихи")
        self.assertIn("not proof",self.v4.provenance(a["id"])["meaning"])
        with self.assertRaises(StoreError):
            self.v4.set_provenance(a["id"],"source_supplied","Книга",False)
        r=self.v4.set_provenance(a["id"],"source_supplied","Книга",True)
        self.assertEqual(r["status"],"source_supplied")
        self.assertIn("no source independently verified",r["meaning"])
        self.assertEqual(len(self.store.list_memories()),1)

    def test_chronology_separates_fiction(self):
        self.memory("Реальное предпочтение")
        self.store.add_event("Ледяной дворец",fiction=True,confirmed=True)
        records=self.v4.chronology()
        self.assertEqual(len(records),2)
        self.assertTrue(any(x["fiction"]==1 and x["source"]=="approved_fiction" for x in records))
        self.assertTrue(any(x["fiction"]==0 for x in records))

    def test_compliment_reactions_rotate(self):
        a,b,c=[self.v4.compliment_reaction(i) for i in range(3)]
        self.assertEqual(len({a["id"],b["id"],c["id"]}),3)
        self.assertEqual(self.v4.compliment_reaction(3)["id"],a["id"])
        with self.assertRaises(StoreError):self.v4.compliment_reaction(-1)

    def test_assessment_requires_approved_goal(self):
        g=self.store.propose_goal("логика","Проверка","5 решений")
        with self.assertRaises(StoreError):
            self.v4.assessment(g["id"],"Два плюс два?","4","4",True)
        self.assertEqual(self.v4.assessments(),[])
        self.store.goal_action(g["id"],"approve")
        with self.assertRaises(StoreError):
            self.v4.assessment(g["id"],"Два плюс два?","4","4",False)

    def test_assessment_exact_match_and_error_review(self):
        g=self.approved_goal()
        p=self.v4.assessment(g["id"],"Два плюс два?","ЧЕТЫРЕ"," четыре  ",True)
        f=self.v4.assessment(g["id"],"Два плюс два?","четыре","пять",True,"Неверное сложение")
        self.assertEqual(p["passed"],1)
        self.assertEqual(f["passed"],0)
        self.assertEqual(len(self.v4.assessments()),2)
        self.assertEqual(self.v4.skill_map()[list(sorted(__import__('core.soul_store',fromlist=['SKILLS']).SKILLS)).index("логика")]["reference_checks"]["tests"],2)

    def test_council_uses_only_evidence(self):
        self.assertEqual(self.v4.development_council(),[])
        g=self.approved_goal("логика")
        self.v4.assessment(g["id"],"Q1","да","нет",True)
        self.v4.assessment(g["id"],"Q2","да","нет",True)
        self.assertFalse(any(x["skill"]=="логика" for x in self.v4.development_council()))
        self.store.goal_action(g["id"],"complete","Две ошибки выявлены")
        c=self.v4.development_council()
        self.assertTrue(any(x["skill"]=="логика" for x in c))
        self.assertEqual(len(self.store.goals()),1)

    def test_strategy_comparison_is_not_applied_to_model(self):
        with self.assertRaises(StoreError):
            self.v4.propose_strategy("логика","Новый метод",60,80,"Результат тестов",False)
        p=self.v4.propose_strategy("логика","Новый метод",60,80,"Проверка 10 заданий",True)
        self.assertEqual(p["status"],"proposed")
        accepted=self.v4.strategy_action(p["id"],"approve")
        self.assertFalse(accepted["applied_to_model"])
        self.assertEqual(self.v4.strategy_action(p["id"],"rollback")["status"],"rolled_back")

    def test_strategy_invalid_numbers_or_skill(self):
        for baseline in (-1,101,float("nan"),True,None):
            with self.assertRaises(StoreError):
                self.v4.propose_strategy("логика","Тест",baseline,80,"Проба",True)
        self.assertEqual(self.v4.strategies(),[])

    def test_diary_uses_existing_audit_without_new_history(self):
        goal=self.approved_goal("логика")
        self.v4.assessment(goal["id"],"Q","А","Б",True,"Опечатка")
        d=self.v4.diary()
        self.assertTrue(any(x["action"]=="academy_assessment_recorded" for x in d))
        self.assertTrue(any(x["action"]=="goal_approved" for x in d))
        self.assertFalse(any("question" in x for x in d))

    def test_disputed_memories_cannot_enter_persona_prompt(self):
        contested=self.memory("Я люблю кофе со льдом",kind="preference")
        self.v4.set_provenance(contested["id"],"owner_disputed","",True)
        trusted=self.memory("Я люблю зеленый чай",kind="preference")
        engine=SoryukaEngine(self.store,FakeCloud())
        cloud=engine.cloud
        engine.chat("Что я люблю пить?",intent="greeting")
        prompt=cloud.calls[-1][0]["content"]
        self.assertIn("Я люблю зеленый чай",prompt)
        self.assertNotIn("Я люблю кофе со льдом",prompt)
        self.assertEqual(len(self.v4.confirmed_memories()),1)
        self.assertTrue(any(row["id"]==contested["id"] for row in self.v4.memory_index("кофе")))

    def test_disputed_traditions_are_not_in_relationship_context(self):
        item=self.memory("Сомнительная традиция",kind="tradition")
        self.v4.set_provenance(item["id"],"source_supplied","Пользователь указал ссылку",True)
        self.assertEqual(self.v4.relationship_context()["approved_memories"],[])

    def test_chat_uses_relevant_memory_and_stays_same_personality(self):
        self.memory("Люблю белый чай",kind="preference")
        cloud=FakeCloud()
        engine=SoryukaEngine(self.store,cloud)
        out=engine.chat("Какой чай я люблю?",intent="affection")
        self.assertIn("С возвращением",out["answer"])
        sys_msg=cloud.calls[-1][0]["content"]
        self.assertIn("Люблю белый чай",sys_msg)
        self.assertIn("единственного хозяина",sys_msg)
        self.assertEqual(engine.profile()["v4_selected_features"],25)

    def test_compliment_changes_avatar_metadata_without_fake_action(self):
        engine=SoryukaEngine(self.store,FakeCloud())
        out=engine.chat("Ты прекрасна",intent="compliment")
        self.assertIn(out["avatar"]["gesture"],{"ear_tilt","small_nod","tail_sway"})
        self.assertEqual(out["avatar"]["emotion"],"play")
        self.assertEqual(engine.profile()["tail_count"],9)

    def test_no_external_cost_for_local_memory_and_academy(self):
        cloud=FakeCloud();SoryukaEngine(self.store,cloud)
        a=self.memory("Памятный ритуал")
        self.v4.memory_index("ритуал")
        self.v4.review_candidates(a["id"])
        self.v4.skill_map()
        self.v4.development_council()
        self.assertEqual(cloud.calls,[])


class API4Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self._old=os.environ.get("SORYUKA_API_TOKEN")
        os.environ["SORYUKA_API_TOKEN"]="test-v4-token"
        self.db=SoulStore(Path(self.temp.name)/"s.db")
        self.http=ThreadingHTTPServer(("127.0.0.1",0),AppHandler)
        self.http.store=self.db
        self.http.engine=SoryukaEngine(self.db,FakeCloud())
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.thread.start()
        self.base=f"http://127.0.0.1:{self.http.server_port}"
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join(timeout=4)
        if self._old is None:os.environ.pop("SORYUKA_API_TOKEN",None)
        else:os.environ["SORYUKA_API_TOKEN"]=self._old
        self.temp.cleanup()

    def request(self,path,data=None,token=True):
        body=json.dumps(data,ensure_ascii=False).encode("utf-8") if data is not None else None
        h={"X-Soryuka-Token":"test-v4-token"} if token else {}
        if body is not None:h["Content-Type"]="application/json"
        req=urllib.request.Request(self.base+path,data=body,headers=h)
        try:
            with urllib.request.urlopen(req,timeout=5) as response:
                return response.status,json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code,json.loads(exc.read())

    def test_memory_search_and_graph_api(self):
        _,a=self.request("/api/memory",{"kind":"fact","text":"Мой любимый чай","confirmed":True})
        _,b=self.request("/api/memory",{"kind":"fact","text":"Чай и ритуал","confirmed":True})
        code,found=self.request("/api/memory/search?q="+quote("чай"))
        self.assertEqual(code,200);self.assertEqual(len(found["items"]),2)
        code,_=self.request("/api/memory/link",{"source_id":a["id"],"target_id":b["id"],
                                                "relation":"related","confirmed":True})
        self.assertEqual(code,200)
        _,graph=self.request("/api/memory/graph")
        self.assertEqual(len(graph["items"]),1)

    def test_memory_merges_and_provenance_require_confirmation(self):
        _,a=self.request("/api/memory",{"kind":"moment","text":"Люблю стихи","confirmed":True})
        _,b=self.request("/api/memory",{"kind":"moment","text":"Люблю стихи","confirmed":True})
        code,_=self.request("/api/memory/merge",{"keep_id":a["id"],"remove_id":b["id"]})
        self.assertEqual(code,400)
        code,_=self.request("/api/memory/merge",{"keep_id":a["id"],"remove_id":b["id"],"confirmed":True})
        self.assertEqual(code,200)
        code,_=self.request("/api/memory/provenance",{"id":a["id"],"status":"owner_disputed",
                                                        "source_note":""})
        self.assertEqual(code,400)

    def test_academy_api_supervised(self):
        _,g=self.request("/api/goal",{"skill":"логика","title":"Логика","criterion":"2 задания"})
        _,_=self.request("/api/goal/action",{"id":g["id"],"action":"approve"})
        code,a=self.request("/api/academy/assessment",{"goal_id":g["id"],"question":"2+2?",
            "reference":"4","answer":"4","confirmed":True})
        self.assertEqual(code,200);self.assertEqual(a["passed"],1)
        _,d=self.request("/api/academy/map")
        self.assertTrue(any(x["skill"]=="логика" and x["reference_checks"]["tests"]==1 for x in d["items"]))
        code,s=self.request("/api/academy/strategy",{"skill":"логика","name":"Медленный разбор",
            "baseline":40,"trial":70,"evidence":"Оценка владельца","confirmed":True})
        self.assertEqual(code,200)
        code,decision=self.request("/api/academy/strategy/action",{"id":s["id"],"action":"approve"})
        self.assertEqual(code,200);self.assertFalse(decision["applied_to_model"])

    def test_reject_unauthorized_api_and_canon_changes(self):
        code,_=self.request("/api/academy/map",token=False)
        self.assertEqual(code,403)
        code,_=self.request("/api/proposal",{"field":"name.russian","value":"другое","reason":"нет"})
        self.assertEqual(code,400)


class V4UIContractTests(unittest.TestCase):
    def test_ui_ids_match_and_source_is_identical_to_checked_js(self):
        html=(ROOT/"ui"/"index.html").read_text(encoding="utf-8")
        src=(ROOT/"ui"/"app.js").read_text(encoding="utf-8")
        self.assertEqual(src,(ROOT/"ui"/"app.ts").read_text(encoding="utf-8"))
        required=["v4-search","v4-link-a","v4-link-b","v4-review-id","v4-evidence-id",
                  "v4-chronology","v4-assessment-goal","v4-strategy-name","v4-council",
                  "v4-map-refresh","v4-skill-map","v4-diary"]
        for item in required:self.assertIn('id="'+item+'"',html)
        self.assertIn("setupV4();",src)
        self.assertIn("const form=event.currentTarget;",src)
        self.assertIn("form.reset();",src)


if __name__=="__main__":unittest.main()
