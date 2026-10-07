#!/usr/bin/env python3
"""
Python Agent Brain - Sprint 0 (Skeleton)

Implements the runtime contract in Section 1 of 02-python-agent-brain.md:
startup sequence, exit codes, signals, the JSONL journal, the hang watchdog
thread and `agent --check`. Sprint 1 adds the WebSocket link with the
Bridge (bridge_link.py, protocol.py; mvp-1-spec.md section 2).
"""

import argparse
import asyncio
import json
import os
import signal
import socket
import sqlite3
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

# --- Exit codes (Section 1.2) ---
EXIT_NORMAL = 0
EXIT_CONFIG = 1
EXIT_PORT_BOUND = 2
EXIT_SECRET = 3
EXIT_SQLITE = 4
EXIT_HANG = 5
EXIT_INTERNAL = 70  # EX_SOFTWARE; not yet listed in Section 1.2
EXIT_SIGINT = 130

MIN_PYTHON = (3, 11)
# The server never listens on anything but loopback; not configurable.
HOST = "127.0.0.1"

DEFAULTS: Dict[str, Any] = {
    "port": 8000,
    "heartbeat_interval": 5.0,
    "watchdog_timeout": 30.0,
    "drain_timeout": 5.0,
    "handshake_timeout": 5.0,
    "dummy_reply_delay": 0.0,
    "dashboard_port": 8001,  # 0 disables the local dashboard
    # Proposal engine (replier.py). Claude is used when ANTHROPIC_API_KEY is in .env.
    "llm_model": "claude-opus-5-5",
    "llm_effort": "low",  # short chat replies; low | medium | high | xhigh | max
    "llm_timeout": 30.0,
    "llm_persona": "",  # optional: who the owner is and how they write
}
LLM_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# Test hook for the 1.4 acceptance criterion: block the event loop after N
# seconds so the watchdog has something to catch.
DEBUG_FREEZE_ENV = "AGENT_DEBUG_FREEZE_AFTER"


def config_dir() -> Path:
    return Path.home() / ".config" / "agent"


def state_dir() -> Path:
    return Path.home() / ".local" / "state" / "agent"


# --- Journal (JSONL, one object per line) ---
class Journal:
    """Append-only JSONL journal, safe to write from the watchdog thread."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        self._lock = threading.Lock()

    def write(self, event: str, level: str = "info", **fields: Any) -> None:
        record = {"ts": time.time(), "level": level, "event": event, **fields}
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        # The watchdog must still be able to record HANG if the lock holder is stuck.
        acquired = self._lock.acquire(timeout=1.0)
        try:
            os.write(self._fd, line)
            if level in ("error", "critical"):
                os.fsync(self._fd)
        finally:
            if acquired:
                self._lock.release()

    def close(self) -> None:
        os.close(self._fd)


# --- Startup steps ---
class StartupError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def load_config() -> Dict[str, Any]:
    import tomllib  # 3.11+, imported after the version check

    config = dict(DEFAULTS)
    path = config_dir() / "config.toml"
    if not path.exists():
        return config
    try:
        with path.open("rb") as f:
            loaded = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise StartupError(EXIT_CONFIG, f"cannot read {path}: {e}")
    unknown = set(loaded) - set(DEFAULTS)
    if unknown:
        raise StartupError(EXIT_CONFIG, f"unknown config keys: {sorted(unknown)}")
    for key, value in loaded.items():
        if not isinstance(value, type(DEFAULTS[key])) or isinstance(value, bool):
            raise StartupError(EXIT_CONFIG, f"config key {key!r} has the wrong type")
        config[key] = value
    if config["llm_effort"] not in LLM_EFFORTS:
        raise StartupError(EXIT_CONFIG, f"llm_effort must be one of {LLM_EFFORTS}")
    if not 0 < config["heartbeat_interval"] < config["watchdog_timeout"]:
        raise StartupError(EXIT_CONFIG, "need 0 < heartbeat_interval < watchdog_timeout")
    return config


def read_env() -> Dict[str, str]:
    """Key=value lines of ~/.config/agent/.env, which must have mode 600."""
    path = config_dir() / ".env"
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        raise StartupError(EXIT_SECRET, f"{path} not found")
    if stat.S_IMODE(mode) & 0o077:
        raise StartupError(EXIT_SECRET, f"{path} must have mode 600")
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep:
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_secret() -> bytes:
    """Read API_SECRET (64 hex chars = 256 bits) from ~/.config/agent/.env."""
    path = config_dir() / ".env"
    secret_hex = read_env().get("API_SECRET")
    if secret_hex is None:
        raise StartupError(EXIT_SECRET, f"API_SECRET missing in {path}")
    try:
        secret = bytes.fromhex(secret_hex)
    except ValueError:
        raise StartupError(EXIT_SECRET, "API_SECRET is not hex")
    if len(secret) != 32:
        raise StartupError(EXIT_SECRET, "API_SECRET must be 256 bits (64 hex chars)")
    return secret


def open_memory() -> sqlite3.Connection:
    path = state_dir() / "memory.db"
    try:
        conn = sqlite3.connect(path, check_same_thread=False)
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    except sqlite3.Error as e:
        raise StartupError(EXIT_SQLITE, f"cannot open {path}: {e}")
    if mode.lower() != "wal":
        conn.close()
        raise StartupError(EXIT_SQLITE, f"{path} refused WAL mode ({mode})")
    return conn


# --- Watchdog thread (the third concurrency exception in 1.3) ---
class Heartbeat:
    def __init__(self) -> None:
        self._last = time.monotonic()

    def beat(self) -> None:
        self._last = time.monotonic()

    def age(self) -> float:
        return time.monotonic() - self._last


WATCHDOG_TICK = 1.0
# A tick that overruns by this much means the whole process was frozen
# (Android cgroup freezer, SIGSTOP), not that the event loop hung.
SUSPEND_GAP = 1.0


def start_watchdog(heartbeat: Heartbeat, journal: Journal, timeout: float,
                   stop: threading.Event) -> threading.Thread:
    def run() -> None:
        last_tick = resumed_at = time.monotonic()
        while not stop.wait(WATCHDOG_TICK):
            now = time.monotonic()
            gap = now - last_tick
            last_tick = now
            if gap > WATCHDOG_TICK + SUSPEND_GAP:
                # Both threads were frozen; the stale heartbeat says nothing
                # about the loop. Give it a full timeout from the resume.
                resumed_at = now
                journal.write("process_resumed", level="warning", frozen_seconds=round(gap, 1))
            silent = min(heartbeat.age(), now - resumed_at)
            if silent > timeout:
                journal.write("HANG", level="critical",
                              seconds_without_heartbeat=round(heartbeat.age(), 1))
                os._exit(EXIT_HANG)

    thread = threading.Thread(target=run, name="hang-watchdog", daemon=True)
    thread.start()
    return thread


# --- Async runtime ---
async def run_agent(config: Dict[str, Any], journal: Journal, memory: sqlite3.Connection,
                    heartbeat: Heartbeat, secret: bytes) -> int:
    from websockets.asyncio.server import serve

    from bridge_link import BridgeLink
    from dashboard import Dashboard, make_token, write_url_file
    from replier import make_replier
    from stats import AgentStats

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    received: Dict[str, Optional[int]] = {"signal": None}

    def on_stop(sig: signal.Signals) -> None:
        if received["signal"] is None:
            received["signal"] = sig
            journal.write("signal", name=sig.name)
        stop.set()

    def on_hup() -> None:
        try:
            config.update(load_config())
            journal.write("config_reloaded")
        except StartupError as e:
            journal.write("config_reload_failed", level="error", reason=str(e))

    loop.add_signal_handler(signal.SIGTERM, on_stop, signal.SIGTERM)
    loop.add_signal_handler(signal.SIGINT, on_stop, signal.SIGINT)
    loop.add_signal_handler(signal.SIGHUP, on_hup)

    # Step 6: bind the real listening socket; failure means exit 2.
    try:
        stats = AgentStats(state_dir() / "paused")
        replier = make_replier(config, read_env().get("ANTHROPIC_API_KEY"))
        stats.brain = config["llm_model"] if replier.name == "claude" else "dummy"
        link = BridgeLink(secret, config, journal, stats, replier)
        server = await serve(link.handle, HOST, config["port"], max_size=64 * 1024)
    except OSError as e:
        journal.write("port_bind_failed", level="error", port=config["port"], reason=str(e),
                      note="port taken: possible squatting by another app (mvp-1-spec 2.2)")
        return EXIT_PORT_BOUND
    journal.write("listening", host=HOST, port=config["port"])
    journal.write("brain", engine=replier.name, model=stats.brain)
    if stats.paused:
        journal.write("emergency_pause_active", level="warning")

    # The dashboard is optional: failing to bind it never stops the agent.
    dash_server = None
    if config["dashboard_port"]:
        token = make_token()
        dash = Dashboard(token, config["dashboard_port"], stats, config,
                         state_dir() / "journal.jsonl", journal)
        try:
            dash_server = await asyncio.start_server(dash.handle, HOST, config["dashboard_port"], limit=8192)
            write_url_file(state_dir(), config["dashboard_port"], token)
            journal.write("dashboard_listening", port=config["dashboard_port"])
        except OSError as e:
            journal.write("dashboard_bind_failed", level="warning", port=config["dashboard_port"], reason=str(e))

    async def heartbeat_task() -> None:
        freeze_after = os.environ.get(DEBUG_FREEZE_ENV)
        started = time.monotonic()
        while not stop.is_set():
            heartbeat.beat()
            if freeze_after and time.monotonic() - started >= float(freeze_after):
                time.sleep(3600)  # deliberately blocks the loop (test hook)
            try:
                await asyncio.wait_for(stop.wait(), config["heartbeat_interval"])
            except asyncio.TimeoutError:
                pass

    async def serve_task() -> None:
        try:
            await stop.wait()
        finally:
            server.close()  # closes open sessions with 1001 (going away)
            await server.wait_closed()
            if dash_server is not None:
                dash_server.close()
                (state_dir() / "dashboard.url").unlink(missing_ok=True)
            await replier.close()

    # Step 7: one TaskGroup for the main loop.
    async with asyncio.TaskGroup() as tg:
        tg.create_task(heartbeat_task(), name="heartbeat")
        tg.create_task(serve_task(), name="server")

    # Graceful shutdown: nothing is queued in Sprint 0, but keep the bounded drain.
    try:
        async with asyncio.timeout(config["drain_timeout"]):
            await asyncio.to_thread(memory.close)
    except TimeoutError:
        journal.write("drain_timeout", level="error")
    journal.write("shutdown", signal=received["signal"].name if received["signal"] else None)
    return EXIT_SIGINT if received["signal"] == signal.SIGINT else EXIT_NORMAL


# --- agent --check (Section 1.5) ---
def check_environment() -> Dict[str, bool]:
    results = {"python_ok": sys.version_info >= MIN_PYTHON}

    try:
        import websockets  # noqa: F401
        results["websockets_ok"] = True
    except ImportError:
        results["websockets_ok"] = False

    try:
        load_secret()
        results["secret_ok"] = True
    except StartupError:
        results["secret_ok"] = False

    # No side effects: never create memory.db; open read-only if it exists.
    db = state_dir() / "memory.db"
    try:
        if db.exists():
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            ok = conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            conn.close()
            results["sqlite_ok"] = ok
        else:
            parent = next(p for p in [db.parent, *db.parents] if p.exists())
            results["sqlite_ok"] = os.access(parent, os.W_OK)
    except sqlite3.Error:
        results["sqlite_ok"] = False

    try:
        port = load_config()["port"]
    except StartupError:
        port = DEFAULTS["port"]
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((HOST, port))
        results["port_free"] = True
    except OSError:
        results["port_free"] = False
    finally:
        sock.close()
    return results


# --- Entry point ---
def main() -> int:
    parser = argparse.ArgumentParser(description="Android Agent Python Brain")
    parser.add_argument("--check", action="store_true",
                        help="run pre-flight checks, print key=value lines, exit 0 only if all pass")
    parser.add_argument("--dashboard-url", action="store_true",
                        help="print the running agent's private dashboard link")
    args = parser.parse_args()

    if args.dashboard_url:
        url_file = state_dir() / "dashboard.url"
        if not url_file.exists():
            print("dashboard not running (is the agent started?)", file=sys.stderr)
            return EXIT_CONFIG
        print(url_file.read_text().strip())
        return EXIT_NORMAL

    if args.check:
        results = check_environment()
        for key, ok in results.items():
            print(f"{key}={'true' if ok else 'false'}")
        return EXIT_NORMAL if all(results.values()) else EXIT_CONFIG

    if sys.version_info < MIN_PYTHON:
        print(f"Python {'.'.join(map(str, MIN_PYTHON))}+ required", file=sys.stderr)
        return EXIT_CONFIG

    # Step 1: config. The journal does not exist yet, so errors go to stderr.
    try:
        config = load_config()
    except StartupError as e:
        print(f"config error: {e}", file=sys.stderr)
        return e.code

    # Step 2: journal. Nothing is recorded before this point.
    journal = Journal(state_dir() / "journal.jsonl")
    journal.write("starting", pid=os.getpid(), python=sys.version.split()[0])

    stop_watchdog = threading.Event()
    try:
        # Steps 3 and 4: secret, then SQLite.
        secret = load_secret()
        memory = open_memory()

        heartbeat = Heartbeat()
        start_watchdog(heartbeat, journal, config["watchdog_timeout"], stop_watchdog)
        return asyncio.run(run_agent(config, journal, memory, heartbeat, secret))
    except StartupError as e:
        journal.write("startup_failed", level="error", code=e.code, reason=str(e))
        return e.code
    except BaseException as e:  # noqa: BLE001 - never exit 0 on an unexpected failure
        journal.write("crashed", level="critical", error=repr(e))
        return EXIT_INTERNAL
    finally:
        stop_watchdog.set()
        journal.close()


if __name__ == "__main__":
    sys.exit(main())
