"""Local dashboard: a read-mostly control panel served on 127.0.0.1.

Any app on the phone can reach loopback ports, so every request needs the
per-start token from ~/.local/state/agent/dashboard.url, and the Host header
must be loopback (blocks DNS rebinding from a web page). Sending a reply is
NOT possible from here: that stays on the Bridge notification behind the
device unlock (mvp-1-spec 3.4). The only control is the emergency pause.
"""

import asyncio
import hmac
import json
import os
import resource
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, urlsplit

PAGE = Path(__file__).resolve().parent / "dashboard.html"
MAX_REQUEST = 8192
JOURNAL_TAIL_BYTES = 64 * 1024
# Journal fields safe to show; message text is never in the journal anyway.
EVENT_FIELDS = ("event", "ts", "level", "reason", "peer", "reply_token", "event_id",
                "text_length", "port", "name", "code", "frozen_seconds")


def make_token() -> str:
    return secrets.token_urlsafe(24)


def write_url_file(state_dir: Path, port: int, token: str) -> Path:
    path = state_dir / "dashboard.url"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"http://127.0.0.1:{port}/?token={token}\n")
    os.chmod(path, 0o600)
    return path


def journal_tail(path: Path, limit: int = 120) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - JOURNAL_TAIL_BYTES))
        lines = f.read().decode("utf-8", "replace").splitlines()
    if size > JOURNAL_TAIL_BYTES:
        lines = lines[1:]  # first line is probably cut
    out = []
    for line in lines[-limit:]:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        out.append({k: rec[k] for k in EVENT_FIELDS if k in rec})
    return out


def process_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {"pid": os.getpid(), "python": sys.version.split()[0]}
    usage = resource.getrusage(resource.RUSAGE_SELF)
    info["cpu_s"] = round(usage.ru_utime + usage.ru_stime, 2)
    try:
        status = Path("/proc/self/status").read_text()
        fields = dict(l.split(":", 1) for l in status.splitlines() if ":" in l)
        info["rss_kb"] = int(fields["VmRSS"].split()[0])
        info["threads"] = int(fields["Threads"])
    except (OSError, KeyError, ValueError):
        pass
    return info


class Dashboard:
    def __init__(self, token: str, port: int, stats: Any, config: Dict[str, Any],
                 journal_path: Path, journal: Any):
        self._token = token
        self._port = port
        self._stats = stats
        self._config = config
        self._journal_path = journal_path
        self._journal = journal
        self._allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(5):
                raw = await reader.readuntil(b"\r\n\r\n")
            status, ctype, body, extra = self._route(raw)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, ValueError):
            status, ctype, body, extra = 400, "text/plain; charset=utf-8", b"bad request", {}
        try:
            writer.write(self._response(status, ctype, body, extra))
            await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    def _route(self, raw: bytes) -> Tuple[int, str, bytes, Dict[str, str]]:
        if len(raw) > MAX_REQUEST:
            raise ValueError("request too large")
        head = raw.decode("latin-1").split("\r\n")
        method, target, _version = head[0].split(" ", 2)
        headers = {}
        for line in head[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        if headers.get("host") not in self._allowed_hosts:
            return self._text(403, "forbidden host")
        url = urlsplit(target)
        token = parse_qs(url.query).get("token", [""])[0]
        if not hmac.compare_digest(token.encode(), self._token.encode()):
            return self._text(403, "forbidden")

        if method == "GET" and url.path == "/":
            return self._page()
        if method == "GET" and url.path == "/api/state":
            return self._json(self.state())
        if method == "POST" and url.path in ("/api/pause", "/api/resume"):
            paused = url.path == "/api/pause"
            self._stats.set_paused(paused)
            self._journal.write("emergency_pause" if paused else "emergency_resume",
                                level="warning" if paused else "info")
            return self._json({"paused": self._stats.paused})
        return self._text(404, "not found")

    def state(self) -> Dict[str, Any]:
        snap = self._stats.snapshot()
        snap["process"] = process_info()
        snap["config"] = {"port": self._config["port"], "dashboard_port": self._port,
                          "handshake_timeout": self._config["handshake_timeout"],
                          "watchdog_timeout": self._config["watchdog_timeout"]}
        snap["events"] = journal_tail(self._journal_path)
        snap["now"] = time.time()
        return snap

    def _page(self) -> Tuple[int, str, bytes, Dict[str, str]]:
        nonce = secrets.token_urlsafe(16)
        html = PAGE.read_text(encoding="utf-8").replace("__NONCE__", nonce)
        csp = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
               "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; "
               "frame-ancestors 'none'")
        return 200, "text/html; charset=utf-8", html.encode("utf-8"), {"Content-Security-Policy": csp}

    @staticmethod
    def _json(obj: Any) -> Tuple[int, str, bytes, Dict[str, str]]:
        return 200, "application/json", json.dumps(obj, ensure_ascii=False).encode("utf-8"), {}

    @staticmethod
    def _text(status: int, text: str) -> Tuple[int, str, bytes, Dict[str, str]]:
        return status, "text/plain; charset=utf-8", text.encode("utf-8"), {}

    @staticmethod
    def _response(status: int, ctype: str, body: bytes, extra: Dict[str, str]) -> bytes:
        reason = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found"}[status]
        headers = {
            "Content-Type": ctype,
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Connection": "close",
            **extra,
        }
        head = f"HTTP/1.1 {status} {reason}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        return (head + "\r\n").encode("latin-1") + body
