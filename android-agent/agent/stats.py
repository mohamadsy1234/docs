"""Live agent state for the dashboard: in memory only, never written to disk.

Message text and sender names live here only while a proposal is pending,
so the dashboard's shadow inbox can show them; the journal never has them.
"""

import time
from collections import Counter, deque
from pathlib import Path
from typing import Any, Dict, Optional

MAX_PENDING = 200


class AgentStats:
    def __init__(self, pause_flag: Path):
        self.started_at = time.time()
        self.counters: Counter = Counter()
        self.latencies_ms: deque = deque(maxlen=60)
        self.bridge_since: Optional[float] = None
        self.bridge_peer: Optional[str] = None
        self.pending: Dict[str, Dict[str, Any]] = {}
        self.brain = "dummy"  # proposal engine: "dummy" or the Claude model id
        self.last_failure: Optional[str] = None
        self._pause_flag = pause_flag

    # --- emergency pause (persists across restarts) ---
    @property
    def paused(self) -> bool:
        return self._pause_flag.exists()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._pause_flag.touch(mode=0o600, exist_ok=True)
        else:
            self._pause_flag.unlink(missing_ok=True)

    # --- events from the Bridge link ---
    def session_opened(self, peer: str) -> None:
        self.counters["sessions"] += 1
        self.bridge_since = time.time()
        self.bridge_peer = peer

    def session_closed(self) -> None:
        self.bridge_since = None
        self.bridge_peer = None

    def incoming(self, token: str, sender: str, text: str) -> None:
        self.counters["incoming"] += 1
        if len(self.pending) >= MAX_PENDING:
            self.pending.pop(next(iter(self.pending)))
        self.pending[token] = {"sender": sender, "text": text,
                               "received_at": time.time(), "proposal": None}

    def proposal_sent(self, token: str, text: str) -> None:
        self.counters["proposals"] += 1
        item = self.pending.get(token)
        if item is not None:
            item["proposal"] = text
            self.latencies_ms.append(round((time.time() - item["received_at"]) * 1000))

    def proposal_failed(self, token: str, reason: str) -> None:
        self.counters["proposal_failed"] += 1
        self.last_failure = reason
        item = self.pending.get(token)
        if item is not None:
            item["failed"] = reason

    def resolved(self, token: str, outcome: str) -> None:
        self.counters[outcome] += 1
        self.pending.pop(token, None)

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "paused": self.paused,
            "brain": self.brain,
            "last_failure": self.last_failure,
            "uptime_s": round(now - self.started_at),
            "bridge": {"connected": self.bridge_since is not None,
                       "since_s": round(now - self.bridge_since) if self.bridge_since else None},
            "counters": dict(self.counters),
            "latency_ms": list(self.latencies_ms),
            "pending": [
                {"token": t[:10], "sender": p["sender"], "text": p["text"],
                 "proposal": p["proposal"], "failed": p.get("failed"), "age_s": round(now - p["received_at"])}
                for t, p in reversed(list(self.pending.items()))
            ],
        }
