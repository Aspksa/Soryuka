"""One portable launcher for Windows/Linux: local Python API + Node frontend, no installs."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import webbrowser

ROOT=Path(__file__).resolve().parent


def port(preferred:int,exclude:int|None=None)->int:
    for candidate in (preferred,0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1",candidate))
                selected=s.getsockname()[1]
                if selected!=exclude:
                    return selected
            except OSError:
                pass
    raise RuntimeError("No available local ports")


def lock_instance():
    lock_path=ROOT/"data"/".launch.lock"
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    lock=open(lock_path,"a+b")
    lock.seek(0)
    lock.write(b"X")
    lock.flush()
    lock.seek(0)
    try:
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError as exc:
        lock.close()
        raise RuntimeError("Soryuka is already running") from exc
    return lock


def ready(port_number:int)->bool:
    with socket.socket() as s:
        s.settimeout(0.15)
        return s.connect_ex(("127.0.0.1",port_number))==0


def main():
    if sys.version_info<(3,11):
        raise RuntimeError("Python 3.11 or newer is required")
    node=shutil.which("node")
    if not node:
        raise RuntimeError("Node.js not found. Only Node for the frontend is required.")
    guard=lock_instance()
    api_port=port(53554)
    ui_port=port(53555,exclude=api_port)
    env=os.environ.copy()
    env.update({"SORYUKA_API_PORT":str(api_port),"SORYUKA_UI_PORT":str(ui_port),
                "SORYUKA_API_TOKEN":secrets.token_urlsafe(40),
                "PYTHONIOENCODING":"utf-8"})
    processes=[]
    try:
        processes.append(subprocess.Popen([sys.executable,"-m","core.server"],cwd=ROOT,env=env))
        processes.append(subprocess.Popen([node,str(ROOT/"ui"/"server.mjs")],cwd=ROOT,env=env))
        for _ in range(100):
            if any(p.poll() is not None for p in processes):
                raise RuntimeError("A Soryuka process exited during startup")
            if ready(api_port) and ready(ui_port):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Soryuka failed to start")
        url="http://127.0.0.1:"+str(ui_port)
        print("Soryuka started:",url,"(Ctrl+C to stop)",flush=True)
        if env.get("SORYUKA_NO_BROWSER")!="1":
            webbrowser.open(url)
        while all(p.poll() is None for p in processes):
            time.sleep(0.3)
        raise RuntimeError("A Soryuka process exited unexpectedly")
    except KeyboardInterrupt:
        print("Shutting down Soryuka...",flush=True)
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
        for p in processes:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        guard.close()


if __name__=="__main__":
    try:
        main()
    except (OSError,RuntimeError) as exc:
        print("Launch error:",str(exc),file=sys.stderr)
        sys.exit(1)
