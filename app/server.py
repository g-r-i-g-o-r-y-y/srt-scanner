"""
Local web server for SRT Scanner: serves ui.html and the JSON API on 127.0.0.1 only.
The work itself is in core.py.
"""
import json
import os
import threading
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from app import core
from app.core import accept_word, apply, check, new_run, run_dir, save_upload

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def local_only(self):
        # refuse requests a web page on another site could forge (DNS rebinding, cross-site POSTs)
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        if host not in ("127.0.0.1", "localhost"):
            return False
        return origin is None or urlparse(origin).hostname in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self.local_only():
            return self.send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        try:
            if u.path == "/":
                return self.send(200, open(UI, "rb").read(), "text/html; charset=utf-8")
            if u.path == "/api/info":
                return self.send(200, {"data": core.DATA})
            if u.path == "/api/download":
                q = parse_qs(u.query)
                d = run_dir(q["run"][0])
                rel = q["file"][0]
                allowed = {"report.md": os.path.join(d, "report.md")}
                cor = os.path.join(d, "corrected")
                if os.path.isdir(cor):
                    allowed.update({x: os.path.join(cor, x) for x in os.listdir(cor)})
                if rel not in allowed or not os.path.exists(allowed[rel]):
                    return self.send(404, {"error": "not found"})
                ctype = "application/zip" if rel.endswith(".zip") else "text/plain; charset=utf-8"
                return self.send(200, open(allowed[rel], "rb").read(), ctype,
                                 {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(rel)}"})
            return self.send(404, {"error": "not found"})
        except (KeyError, ValueError) as e:
            return self.send(400, {"error": str(e)})

    def do_POST(self):
        if not self.local_only():
            return self.send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/api/run":
                return self.send(200, {"run": new_run()})
            if u.path == "/api/upload":
                saved = save_upload(q["run"][0], unquote(q["name"][0]), self.body())
                return self.send(200, {"saved": saved})
            data = json.loads(self.body() or b"{}")
            if u.path == "/api/check":
                return self.send(200, check(data["run"], bool(data.get("recheck"))))
            if u.path == "/api/apply":
                return self.send(200, apply(data["run"], data.get("picked", []), data.get("edits", {})))
            if u.path == "/api/accept-word":
                return self.send(200, accept_word(data["word"]))
            return self.send(404, {"error": "not found"})
        except (KeyError, ValueError, RuntimeError, zipfile.BadZipFile) as e:
            return self.send(400, {"error": str(e)})


def serve(data_dir, port=0, open_browser=True):
    core.DATA = os.path.abspath(os.path.expanduser(data_dir))
    os.makedirs(os.path.join(core.DATA, "runs"), exist_ok=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"SRT Scanner is running at {url}")
    print(f"Files are kept in {core.DATA}")
    print("Close this window (or press Ctrl+C) to stop.")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
