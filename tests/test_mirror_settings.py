"""Regression: graphics sliders survive restarts, dynamic ports and invalid settings.

All persistence goes into the existing Soryuka SQLite; no additional services.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.mirror_settings import DEFAULTS,read_graphics,write_graphics,validate_graphics
from core.soul_store import SoulStore, StoreError
from core.soul_engine import SoryukaEngine
from core.server import AppHandler


class FakeCloud:
    def ready(self):return False


class GraphicsSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/"папка с пробелом"/"memory.sqlite3"
        self.store=SoulStore(self.path)
    def tearDown(self):self.temp.cleanup()

    def test_original_values_on_new_install(self):
        self.assertEqual(read_graphics(self.store)["settings"],DEFAULTS)
        self.assertEqual(read_graphics(self.store)["source"],"defaults")

    def test_settings_survive_new_store_instance_and_keep_same_db(self):
        payload={"fog":0.00125,"light":1.8,"speed":0.5}
        write_graphics(self.store,payload)
        reopened=SoulStore(self.path)
        self.assertEqual(read_graphics(reopened)["settings"],payload)
        self.assertEqual(read_graphics(reopened)["source"],"sqlite")
        with reopened.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM mirror_graphics_settings").fetchone()[0],1)
            self.assertTrue(c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='memories'").fetchone()[0])

    def test_reset_is_original_values_in_same_sqlite(self):
        write_graphics(self.store,{"fog":0.002,"light":0.4,"speed":1.65})
        write_graphics(self.store,dict(DEFAULTS))
        self.assertEqual(read_graphics(SoulStore(self.path))["settings"],DEFAULTS)

    def test_exact_validation_rejects_wrong_ranges_types_keys_and_infinity(self):
        invalid=[
            None,[],{},{"fog":0.001},dict(DEFAULTS,hidden=1),
            {"fog":-0.001,"light":1,"speed":1},
            {"fog":0.001,"light":3,"speed":1},
            {"fog":0.001,"light":1,"speed":0},
            {"fog":"0.001","light":1,"speed":1},
            {"fog":True,"light":1,"speed":1},
            {"fog":float("nan"),"light":1,"speed":1},
            {"fog":float("inf"),"light":1,"speed":1}
        ]
        for payload in invalid:
            with self.subTest(payload=payload),self.assertRaises(StoreError):
                write_graphics(self.store,payload)
        self.assertEqual(read_graphics(self.store)["settings"],DEFAULTS)

    def test_rounding_to_slider_steps(self):
        self.assertEqual(
            validate_graphics({"fog":0.00083,"light":1.57,"speed":0.43}),
            {"fog":0.00085,"light":1.6,"speed":0.45}
        )

    def test_corrupt_sqlite_settings_are_ignored_not_executed(self):
        read_graphics(self.store)
        with self.store.db() as c:
            c.execute("""INSERT INTO mirror_graphics_settings(name,value_json,updated_at)
                         VALUES('scene','not-json','2000-01-01')""")
        self.assertEqual(read_graphics(self.store)["settings"],DEFAULTS)
        self.assertEqual(read_graphics(self.store)["source"],"defaults")

    def test_personality_canon_and_memory_unmodified(self):
        base=(ROOT/"character"/"soryuka.json").read_bytes()
        memory=self.store.add_memory("fact","Реальная запись",True)
        write_graphics(self.store,{"fog":0.003,"light":0.2,"speed":2.0})
        self.assertEqual(base,(ROOT/"character"/"soryuka.json").read_bytes())
        self.assertEqual(self.store.list_memories()[0]["id"],memory["id"])


class GraphicsSettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=os.environ.get("SORYUKA_API_TOKEN")
        os.environ["SORYUKA_API_TOKEN"]="graphics-settings-secret"
        self.server=ThreadingHTTPServer(("127.0.0.1",0),AppHandler)
        self.server.store=SoulStore(Path(self.temp.name)/"graphics.db")
        self.server.engine=SoryukaEngine(self.server.store,FakeCloud())
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url="http://127.0.0.1:"+str(self.server.server_port)+"/api/mirror/settings"
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        if self.old is None:os.environ.pop("SORYUKA_API_TOKEN",None)
        else:os.environ["SORYUKA_API_TOKEN"]=self.old
        self.temp.cleanup()

    def call(self,body=None,token=True):
        data=json.dumps(body).encode("utf-8") if body is not None else None
        headers={"X-Soryuka-Token":"graphics-settings-secret"} if token else {}
        if data is not None:headers["Content-Type"]="application/json"
        req=urllib.request.Request(self.url,data=data,headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=5) as res:
                return res.status,json.loads(res.read())
        except urllib.error.HTTPError as exc:
            return exc.code,json.loads(exc.read())

    def test_protected_get_post_and_persistence(self):
        status,_=self.call(token=False)
        self.assertEqual(status,403)
        status,_=self.call({"settings":dict(DEFAULTS)},token=False)
        self.assertEqual(status,403)
        status,first=self.call()
        self.assertEqual(status,200)
        self.assertEqual(first["source"],"defaults")
        saved={"fog":0.001,"light":1.3,"speed":1.4}
        status,out=self.call({"settings":saved})
        self.assertEqual(status,200)
        self.assertEqual(out["source"],"sqlite")
        self.assertEqual(out["settings"],saved)
        status,loaded=self.call()
        self.assertEqual(status,200)
        self.assertEqual(loaded["settings"],saved)
        self.assertNotIn("graphics-settings-secret",json.dumps(loaded))

    def test_invalid_values_rejected_without_clobbering(self):
        baseline={"fog":0.00095,"light":1.2,"speed":1.25}
        self.call({"settings":baseline})
        status,_=self.call({"settings":{"fog":20,"light":1,"speed":1}})
        self.assertEqual(status,400)
        status,_=self.call({"settings":{"fog":0.001,"light":True,"speed":1}})
        self.assertEqual(status,400)
        self.assertEqual(self.call()[1]["settings"],baseline)


if __name__=="__main__":
    unittest.main()
