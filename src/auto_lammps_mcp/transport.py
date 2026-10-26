"""Private Unix-socket RPC. All host processes connect to one durable service."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import socketserver
import threading
import time

from .core import Fault, encode

MAX_MESSAGE = 8 * 1024 * 1024


def socket_path(home):
    key = hashlib.sha256(str(Path(home).resolve()).encode()).hexdigest()[:16]
    root = Path("/tmp") / f"alm-{os.getuid()}-{key}"
    if root.exists() and (root.is_symlink() or root.stat().st_uid != os.getuid()):
        raise Fault("socket_ownership", "服务目录不属于当前用户", "检查本机服务目录")
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    return root / "service.sock"


def call(home, method, arguments=None, host=None, client=None):
    path = socket_path(home)
    try:
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(30)
            connection.connect(str(path))
            connection.sendall((encode(dict(protocol=1, method=method, arguments=arguments or {}, host=host, client=client)) + "\n").encode())
            with connection.makefile("rb") as reader:
                line = reader.readline(MAX_MESSAGE + 1)
        if len(line) > MAX_MESSAGE:
            raise Fault("response_too_large", "返回内容超过消息大小", "使用分页读取")
        result = json.loads(line)
    except (FileNotFoundError, ConnectionRefusedError) as exc:
        raise Fault("service_unavailable", "任务服务未运行", "运行 alm start 或 alm install 启动后台") from exc
    except (TimeoutError, ConnectionResetError, json.JSONDecodeError) as exc:
        raise Fault("response_unknown", "未收到完整回执", "先读取任务；变更重试必须保留同一 op_id", "unknown") from exc
    if not result["ok"]:
        error = result["error"]
        raise Fault(error["code"], error["message"], error["next_action"], error["effect"])
    return result["result"]


def serve(home):
    from .service import Service
    from .worker import Worker
    from .identity import runtime_identity, connection_status
    from .responses import bound_response, bound_error
    service = Service(home)
    identity = runtime_identity()
    lock = (service.store.home / "service.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    path = socket_path(service.store.home)
    path.unlink(missing_ok=True)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(30)
            request = {}
            try:
                line = self.rfile.readline(MAX_MESSAGE + 1)
                if len(line) > MAX_MESSAGE:
                    raise Fault("request_too_large", "请求超过大小上限", "使用局部文件修改")
                request = json.loads(line)
                if request.get("protocol") != 1:
                    raise Fault("protocol_mismatch", "工具与服务协议版本不一致", "使用同一发布版本重新连接")
                result = service.invoke(request["method"], request.get("arguments", {}))
                host = request.get("host")
                if host:
                    host = str(host)[:60]
                    client = request.get("client")
                    client = {key: str(client[key])[:128] for key in ("session_id", "version", "fingerprint") if key in client} if isinstance(client, dict) else None
                    receipt = dict(last_call=time.time(), tool=request["method"], client=client)
                    with service.store.transaction() as db:
                        calls = service.store.setting("host_calls", {})
                        previous = calls.get(host, {}).get("client")
                        if client and client != previous:
                            service.store.event(db, None, "host_session_connected", dict(host=host, **connection_status(receipt, identity)))
                        calls[host] = receipt
                        service.store.set_setting(db, "host_calls", calls)
                    if request["method"] == "doctor":
                        result = service.doctor()
                        result["current_connection"] = dict(host=host, **connection_status(receipt, identity))
                    result = bound_response(service.store, request["method"], result)
                response = dict(ok=True, result=result)
            except Fault as exc:
                response = dict(ok=False, error=exc.as_dict())
            except Exception as exc:
                with service.store.transaction() as db:
                    service.store.event(db, None, "service_error", dict(type=type(exc).__name__, message=str(exc)))
                response = dict(ok=False, error=Fault("internal_error", str(exc), "读取事件后使用同一操作编号核对", "unknown").as_dict())
            if not response["ok"] and isinstance(request, dict) and request.get("host"):
                response["error"] = bound_error(service.store, request.get("method", "unknown"), response["error"])
            try:
                self.wfile.write((encode(response) + "\n").encode())
            except BrokenPipeError:
                pass

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    worker = Worker(service)
    threading.Thread(target=worker.loop, daemon=True, name="alm-worker").start()
    try:
        if not worker.ready.wait(timeout=30):
            raise Fault("service_recovery_timeout", "后台尚未完成重启恢复", "读取服务日志后从原任务接续")
        with Server(str(path), Handler) as server:
            path.chmod(0o600)
            server.serve_forever(poll_interval=0.5)
    finally:
        worker.stopping.set()
        path.unlink(missing_ok=True)
        lock.close()

