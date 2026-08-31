#!/usr/bin/env python3
"""DeepSeek hop (:18791) — Grok Bot bindings reach api.deepseek.com without
putting the key in model-bindings.json.

  GET  /health   picker live-model table
  GET  /healthz  doctor probe (no upstream call)
  POST /v1/chat/completions  (and any other path) -> https://api.deepseek.com<same-path>

Key is read from DEEPSEEK_API_KEY or %USERPROFILE%\\.grokbot\\deepseek.env.
Never logged. Never written to bindings.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("deepseek-hop")

HOST = os.environ.get("DEEPSEEK_HOP_HOST", "127.0.0.1")
PORT = int(os.environ.get("DEEPSEEK_HOP_PORT", "18791"))
UPSTREAM = os.environ.get("DEEPSEEK_HOP_UPSTREAM", "https://api.deepseek.com").rstrip("/")
_TIMEOUT = float(os.environ.get("DEEPSEEK_HOP_TIMEOUT", "1800"))
_MAX_BODY = 64 * 1024 * 1024
CATALOG = ["deepseek-v4-flash"]
ENV_CANDIDATES = [
    os.path.join(os.environ.get("USERPROFILE", ""), ".grokbot", "deepseek.env"),
    os.path.join(os.environ.get("HOME", ""), ".grokbot", "deepseek.env"),
    "/home/box/sand-data/deepseek.env",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "deepseek.env"),
]

_KEY = ""
_CTX = ssl.create_default_context()


def load_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    for path in ENV_CANDIDATES:
        if not path:
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("DEEPSEEK_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val
        except OSError:
            continue
    raise SystemExit(
        "deepseek-hop: no DEEPSEEK_API_KEY in env or " + " / ".join(ENV_CANDIDATES)
    )


def rewrite_body(raw: bytes | None) -> bytes | None:
    """Picker sends modelId; DeepSeek wants model. Official slug has no :thinking suffix."""
    if not raw:
        return raw
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        return raw
    if not isinstance(obj, dict):
        return raw
    slug = str(obj.get("model") or obj.get("modelId") or "")
    thinking = False
    if slug.lower().endswith(":thinking"):
        thinking = True
        slug = slug[: slug.rfind(":")]
    if slug:
        obj["model"] = slug
    obj.pop("modelId", None)
    if thinking and "thinking" not in obj:
        obj["thinking"] = {"type": "enabled"}
    return json.dumps(obj).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "deepseek-hop/1"

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def _simple(self, code: int, payload: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _relay(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > _MAX_BODY:
            self._simple(413, b'{"error":"body too large"}')
            return
        body = self.rfile.read(length) if length else None
        if self.command == "POST" and self.path.startswith("/v1/"):
            body = rewrite_body(body)

        url = UPSTREAM + self.path
        req = urllib.request.Request(url, data=body, method=self.command)
        for name, value in self.headers.items():
            if name.lower() in ("host", "authorization", "content-length", "connection", "accept-encoding"):
                continue
            req.add_header(name, value)
        req.add_header("Authorization", "Bearer " + _KEY)
        req.add_header("Accept-Encoding", "identity")
        if body is not None:
            req.add_header("Content-Type", self.headers.get("Content-Type") or "application/json")

        try:
            resp = urllib.request.urlopen(req, timeout=_TIMEOUT, context=_CTX)
        except urllib.error.HTTPError as exc:
            payload = exc.read() or b""
            self.send_response(exc.code)
            ctype = exc.headers.get("Content-Type") if exc.headers else None
            if ctype:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            log.warning("upstream unreachable: %s", type(exc).__name__)
            self._simple(502, b'{"error":{"message":"deepseek api unreachable","type":"hop_error"}}')
            return

        ctype = resp.headers.get("Content-Type", "")
        if "text/event-stream" in ctype or "chunked" in (resp.headers.get("Transfer-Encoding") or ""):
            self.send_response(resp.getcode())
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionAbortedError):
                log.info("client aborted mid-stream")
        else:
            payload = resp.read()
            self.send_response(resp.getcode())
            if ctype:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        resp.close()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._simple(200, json.dumps({
                "ok": True, "service": "deepseek-hop", "port": PORT,
            }).encode())
            return
        if path == "/health":
            self._simple(200, json.dumps({
                "deepseek-official": {
                    "configured": True,
                    "upstream": UPSTREAM,
                    "models": CATALOG,
                    "note": "official api.deepseek.com",
                }
            }).encode())
            return
        self._relay()

    def do_POST(self):
        self._relay()

    def do_DELETE(self):
        self._relay()


def main() -> None:
    global _KEY
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    _KEY = load_key()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("deepseek-hop listening http://%s:%s -> %s (key loaded, len=%d)",
             HOST, PORT, UPSTREAM, len(_KEY))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        raise SystemExit(0)


if __name__ == "__main__":
    main()
