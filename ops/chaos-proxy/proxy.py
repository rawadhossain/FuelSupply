#!/usr/bin/env python3
"""Chaos proxy: forwards to UPSTREAM, optionally corrupting responses.
Data plane on :8000, control plane (POST /__chaos {"mode": ...}) on :8001.
stdlib only.
"""
import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("UPSTREAM", "http://simulator-api:8000")
MODES = {"passthrough", "corrupt_json", "wrong_schema", "slow", "error500"}

_state = {"mode": "passthrough"}
_lock = threading.Lock()


def get_mode():
    with _lock:
        return _state["mode"]


def set_mode(mode):
    with _lock:
        _state["mode"] = mode


def _strip_id(obj):
    if isinstance(obj, dict):
        return {k: _strip_id(v) for k, v in obj.items() if k != "id"}
    if isinstance(obj, list):
        return [_strip_id(v) for v in obj]
    return obj


class DataHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _proxy(self):
        mode = get_mode()

        if mode == "error500":
            body = b'{"error":{"code":"CHAOS_INJECTED","message":"error500 mode"}}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if mode == "slow":
            time.sleep(2)

        length = int(self.headers.get("Content-Length", 0))
        req_body = self.rfile.read(length) if length else None

        url = f"{UPSTREAM}{self.path}"
        req = urllib.request.Request(url, data=req_body, method=self.command)
        for k, v in self.headers.items():
            if k.lower() in ("host", "content-length", "connection"):
                continue
            req.add_header(k, v)

        is_stream = self.path.startswith("/v1/stream")

        try:
            with urllib.request.urlopen(req, timeout=None if is_stream else 15) as resp:
                if is_stream:
                    self.send_response(resp.status)
                    for k, v in resp.headers.items():
                        if k.lower() in ("content-length", "connection", "transfer-encoding"):
                            continue
                        self.send_header(k, v)
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("X-Accel-Buffering", "no")
                    self.end_headers()
                    try:
                        while True:
                            chunk = resp.read(1)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return

                raw = resp.read()
                content_type = resp.headers.get("Content-Type", "")
                status = resp.status

                if mode == "corrupt_json" and "json" in content_type and raw:
                    raw = raw[: max(1, len(raw) // 2)]
                elif mode == "wrong_schema" and "json" in content_type and raw:
                    try:
                        data = json.loads(raw)
                        raw = json.dumps(_strip_id(data)).encode()
                    except Exception:
                        pass

                self.send_response(status)
                for k, v in resp.headers.items():
                    if k.lower() in ("content-length", "connection", "transfer-encoding"):
                        continue
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            self.send_response(e.code)
            for k, v in e.headers.items():
                if k.lower() in ("content-length", "connection", "transfer-encoding"):
                    continue
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except Exception as exc:
            body = json.dumps({"error": {"code": "PROXY_ERROR", "message": str(exc)}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def do_PUT(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()

    def do_PATCH(self):
        self._proxy()


class ControlHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        if self.path != "/__chaos":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw)
            mode = data.get("mode", "passthrough")
        except Exception:
            mode = None
        if mode not in MODES:
            body = json.dumps({"error": f"invalid mode, must be one of {sorted(MODES)}"}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        set_mode(mode)
        body = json.dumps({"mode": mode}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/__chaos":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"mode": get_mode()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    data_srv = ThreadingHTTPServer(("0.0.0.0", 8000), DataHandler)
    ctrl_srv = ThreadingHTTPServer(("0.0.0.0", 8001), ControlHandler)
    t = threading.Thread(target=ctrl_srv.serve_forever, daemon=True)
    t.start()
    print(f"chaos-proxy: data :8000 -> {UPSTREAM}, control :8001")
    data_srv.serve_forever()


if __name__ == "__main__":
    main()
