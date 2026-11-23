"""Node -> Python proxy integration smoke tests without Cloud.ru credentials or third-party packages."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core.server import AppHandler
from core.soul_engine import SoryukaEngine
from core.soul_store import SoulStore


class FakeCloud:
    def ready(self):return True
    def complete(self,messages,max_tokens=768):
        assert messages[-1]["role"]=="user"
        return "С возвращением, мой хозяин.",{"total_tokens":11}


@unittest.skipUnless(shutil.which("node"),"Node.js not available")
class NodeProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.token_old=os.environ.get("SORYUKA_API_TOKEN")
        os.environ["SORYUKA_API_TOKEN"]="test-proxy-token"
        self.api=ThreadingHTTPServer(("127.0.0.1",0),AppHandler)
        self.api.store=SoulStore(Path(self.temp.name)/"storage.sqlite3")
        self.api.engine=SoryukaEngine(self.api.store,FakeCloud())
        self.thread=threading.Thread(target=self.api.serve_forever,daemon=True)
        self.thread.start()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0))
            self.ui_port=sock.getsockname()[1]
        env=os.environ.copy()
        env.update({"SORYUKA_API_PORT":str(self.api.server_port),
                    "SORYUKA_UI_PORT":str(self.ui_port),
                    "SORYUKA_API_TOKEN":"test-proxy-token"})
        self.node=subprocess.Popen([shutil.which("node"),str(ROOT/"ui"/"server.mjs")],
                                   cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.address="http://127.0.0.1:"+str(self.ui_port)
        for attempt in range(80):
            if self.node.poll() is not None:
                self.fail("Node frontend exited at startup")
            try:
                with urllib.request.urlopen(self.address+"/",timeout=.5) as resp:
                    if resp.status==200:
                        break
            except (urllib.error.URLError, TimeoutError):
                time.sleep(.1)
        else:
            self.fail("Node frontend did not become ready")

    def tearDown(self):
        self.node.terminate()
        try:self.node.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.node.kill()
            self.node.wait()
        self.api.shutdown()
        self.api.server_close()
        self.thread.join(timeout=3)
        if self.token_old is None:os.environ.pop("SORYUKA_API_TOKEN",None)
        else:os.environ["SORYUKA_API_TOKEN"]=self.token_old
        self.temp.cleanup()

    def request(self,path,body=None,origin=None):
        headers={}
        data=None
        if body is not None:
            data=json.dumps(body).encode("utf-8")
            headers["Content-Type"]="application/json"
        if origin:headers["Origin"]=origin
        request=urllib.request.Request(self.address+path,data=data,headers=headers)
        try:
            with urllib.request.urlopen(request,timeout=5) as response:
                return response.status,response.read(),response.headers
        except urllib.error.HTTPError as exc:
            return exc.code,exc.read(),exc.headers

    def test_static_page_and_authenticated_api_proxy(self):
        code,html,_=self.request("/")
        self.assertEqual(code,200)
        self.assertIn("霜琉華".encode("utf-8"),html)
        code,payload,_=self.request("/api/health")
        self.assertEqual(code,200)
        self.assertTrue(json.loads(payload)["ok"])

    def test_mirror_static_assets_and_legacy_console(self):
        for route,marker in [("/",b"canvas-container"),("/console.html",b"chat-form"),
                             ("/mirror-original.js",b"THREE.FogExp2"),
                             ("/graphics-settings.js",b"GRAPHICS_STORAGE_KEY"),
                             ("/vendor/three.module.js",b"WebGLRenderer"),
                             ("/vendor/three-addons/controls/OrbitControls.js",b"OrbitControls"),
                             ("/vendor/d3-force-3d.min.js",b"forceSimulation")]:
            status,body,headers=self.request(route)
            self.assertEqual(status,200,route)
            self.assertIn(marker,body,route)
            self.assertNotIn(b"test-proxy-token",body,route)
        status,data,_=self.request("/api/mirror/graph")
        self.assertEqual(status,200)
        self.assertEqual(json.loads(data)["source"],"local_sqlite")

    def test_websocket_streams_original_realtime_graph_without_exposing_secret(self):
        import base64
        import secrets
        key=base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        with socket.create_connection(("127.0.0.1",self.ui_port),timeout=5) as sock:
            sock.settimeout(8)
            sock.sendall((
                "GET /mirror-ws HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{self.ui_port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                f"Origin: {self.address}\r\n\r\n"
            ).encode("ascii"))
            data=b""
            while b"\r\n\r\n" not in data:
                data+=sock.recv(4096)
            headers,payload=data.split(b"\r\n\r\n",1)
            self.assertIn(b"101 Switching Protocols",headers)
            # Initial WebSocket text frame is a real graph snapshot from Python.
            while len(payload)<2:payload+=sock.recv(4096)
            prefix=payload[1]&127
            if prefix==126:
                while len(payload)<4:payload+=sock.recv(4096)
                length=int.from_bytes(payload[2:4],"big");header_size=4
            elif prefix==127:
                while len(payload)<10:payload+=sock.recv(4096)
                length=int.from_bytes(payload[2:10],"big");header_size=10
            else:length=prefix;header_size=2
            while len(payload)<header_size+length:payload+=sock.recv(4096)
            frame=json.loads(payload[header_size:header_size+length])
            self.assertEqual(frame["type"],"reload")
            self.assertEqual(frame["data"]["source"],"local_sqlite")
            self.assertNotIn("test-proxy-token",json.dumps(frame))

    def test_websocket_rejects_cross_origin(self):
        import base64
        with socket.create_connection(("127.0.0.1",self.ui_port),timeout=5) as sock:
            sock.settimeout(4)
            req=(f"GET /mirror-ws HTTP/1.1\r\nHost: 127.0.0.1:{self.ui_port}\r\n"
                 "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                 "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                 "Sec-WebSocket-Version: 13\r\n"
                 "Origin: https://example.com\r\n\r\n")
            sock.sendall(req.encode("ascii"))
            self.assertIn(b"403 Forbidden",sock.recv(1024))

    def test_chat_proxy_keeps_server_token_off_browser(self):
        code,body,_=self.request("/api/session",{})
        self.assertEqual(code,200)
        sid=json.loads(body)["session_id"]
        code,body,_=self.request("/api/chat",{"session_id":sid,"message":"Привет","intent":"affection"})
        self.assertEqual(code,200)
        result=json.loads(body)
        self.assertEqual(result["avatar"]["gesture"],"tail_wrap")
        self.assertEqual(result["answer"],"С возвращением, мой хозяин.")
        self.assertNotIn("test-proxy-token",body.decode())

    def test_cross_origin_write_is_blocked(self):
        code,body,_=self.request("/api/session",{},origin="https://example.com")
        self.assertEqual(code,403)
        self.assertIn(b"Cross-site",body)

    def test_static_path_traversal_is_not_served(self):
        code,body,_=self.request("/private.db")
        self.assertEqual(code,404)
        self.assertNotIn(b"SQLite",body)


if __name__=="__main__":unittest.main()
