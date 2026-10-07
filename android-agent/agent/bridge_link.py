"""WebSocket link with the Bridge app (mvp-1-spec.md, sections 2 and 3).

One authenticated Bridge session at a time. Nothing is read from a peer as a
message until it has passed the mutual handshake; any protocol violation
closes the connection.
"""

import asyncio
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed

import protocol
from protocol import ProtocolError

# Sprint 1: a fixed proposal stands in for the LLM (mvp-1-spec.md, 6).
DUMMY_REPLY = "رد تجريبي من الوكيل (Sprint 1)"
MAX_TEXT = 4096
SEEN_EVENTS = 1000

CLOSE_POLICY = 1008  # policy violation: failed handshake or bad envelope
CLOSE_SUPERSEDED = 4000

# Required string fields per message type from the Bridge (mvp-1-spec.md, 2.3).
FIELDS = {
    "incoming": ("event_id", "reply_token", "sender_name", "text"),
    "notification_sent": ("reply_token", "sent_text"),
    "proposal_ignored": ("reply_token",),
    "notification_expired": ("reply_token",),
}


class _NoStats:
    """Stand-in when no dashboard stats are attached."""

    paused = False

    def __init__(self) -> None:
        from collections import Counter
        self.counters = Counter()

    def __getattr__(self, name: str) -> Callable[..., None]:
        return lambda *args, **kwargs: None


def _check_fields(message: Dict[str, Any]) -> None:
    for name in FIELDS[message["type"]]:
        value = message.get(name)
        if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
            raise ProtocolError(f"{message['type']}.{name} missing or invalid")


class BridgeLink:
    def __init__(self, secret: bytes, config: Dict[str, Any], journal: Any, stats: Any = None):
        self._secret = secret
        self._config = config
        self._journal = journal
        self._stats = stats if stats is not None else _NoStats()
        self._current: Optional[ServerConnection] = None
        self._seen: "OrderedDict[str, None]" = OrderedDict()

    async def handle(self, ws: ServerConnection) -> None:
        peer = ws.remote_address
        try:
            async with asyncio.timeout(self._config["handshake_timeout"]):
                key = await self._handshake(ws)
        except (ProtocolError, TimeoutError) as e:
            self._journal.write("handshake_failed", level="warning", peer=str(peer),
                                reason=str(e) or type(e).__name__)
            self._stats.counters["handshake_failed"] += 1
            await ws.close(CLOSE_POLICY, "handshake failed")
            return
        except ConnectionClosed:
            self._journal.write("handshake_aborted", level="warning", peer=str(peer))
            return

        if self._current is not None:
            # A reconnecting Bridge replaces a half-dead previous session.
            await self._current.close(CLOSE_SUPERSEDED, "superseded")
        self._current = ws
        self._journal.write("session_opened", peer=str(peer))
        self._stats.session_opened(str(peer))
        try:
            await self._serve(ws, protocol.server_channel(key))
        finally:
            if self._current is ws:
                self._current = None
                self._stats.session_closed()
            self._journal.write("session_closed", peer=str(peer))

    async def _handshake(self, ws: ServerConnection) -> bytes:
        nonce_b = protocol.read_hello(await ws.recv())
        frame, nonce_s = protocol.challenge_frame(self._secret, nonce_b)
        await ws.send(frame)
        protocol.read_auth(await ws.recv(), self._secret, nonce_b, nonce_s)
        return protocol.session_key(self._secret, nonce_b, nonce_s)

    async def _serve(self, ws: ServerConnection, channel: protocol.Channel) -> None:
        send_lock = asyncio.Lock()  # one writer at a time keeps seq in order (1.3)

        async def send(message: Dict[str, Any]) -> None:
            async with send_lock:
                await ws.send(channel.seal(message))

        try:
            async with asyncio.TaskGroup() as tg:
                async for raw in ws:
                    message = channel.open(raw)
                    self._dispatch(message, tg, send)
        except* ProtocolError as group:
            self._journal.write("protocol_violation", level="warning",
                                reason=str(group.exceptions[0]))
            self._stats.counters["protocol_violation"] += 1
            await ws.close(CLOSE_POLICY, "protocol violation")
        except* ConnectionClosed:
            pass

    def _dispatch(self, message: Dict[str, Any], tg: asyncio.TaskGroup,
                  send: Callable[[Dict[str, Any]], Any]) -> None:
        kind = message["type"]
        if kind not in FIELDS:
            self._journal.write("unknown_message", level="warning", type=kind)
            return
        _check_fields(message)
        token = message["reply_token"]

        if kind == "incoming":
            event_id = message["event_id"]
            if event_id in self._seen:
                self._journal.write("incoming_duplicate", event_id=event_id)
                self._stats.counters["duplicates"] += 1
                return
            self._seen[event_id] = None
            if len(self._seen) > SEEN_EVENTS:
                self._seen.popitem(last=False)
            # Message text stays out of the journal; only its size is recorded.
            self._journal.write("incoming", event_id=event_id, reply_token=token,
                                text_length=len(message["text"]))
            self._stats.incoming(token, message["sender_name"], message["text"])
            if self._stats.paused:
                # Emergency pause (dashboard kill switch): listen, propose nothing.
                self._journal.write("proposal_withheld", reply_token=token, reason="paused")
                return
            # Each proposal runs on its own task, so a slow LLM call for one
            # sender never delays the next incoming message (1.3).
            tg.create_task(self._propose(token, send))
        else:
            self._journal.write(kind, reply_token=token)
            self._stats.resolved(token, {"notification_sent": "sent", "proposal_ignored": "ignored",
                                         "notification_expired": "expired"}[kind])

    async def _propose(self, token: str, send: Callable[[Dict[str, Any]], Any]) -> None:
        await asyncio.sleep(self._config["dummy_reply_delay"])
        if self._stats.paused:  # paused while this proposal was being prepared
            self._journal.write("proposal_withheld", reply_token=token, reason="paused")
            return
        await send({"type": "propose_reply", "reply_token": token, "proposed_text": DUMMY_REPLY})
        self._journal.write("proposal_sent", reply_token=token)
        self._stats.proposal_sent(token, DUMMY_REPLY)
