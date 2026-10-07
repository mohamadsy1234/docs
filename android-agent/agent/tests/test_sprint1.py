"""Sprint 1 acceptance tests for the agent side of mvp-1-spec.md (section 2, 6).

The agent runs as a real process; tools/fake_bridge.py plays the Bridge.
Bridge-only behaviour (notification handling, shadow UI) is Kotlin and is
not covered here.
Run with: python3 -m unittest discover -s android-agent/agent/tests -v
"""

import asyncio
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))
sys.path.insert(0, str(AGENT_DIR / "tools"))

from websockets.asyncio.client import connect  # noqa: E402
from websockets.asyncio.server import serve  # noqa: E402
from websockets.exceptions import ConnectionClosed  # noqa: E402

import protocol  # noqa: E402
from fake_bridge import FakeBridge  # noqa: E402

CLOSE_POLICY = 1008
CLOSE_SUPERSEDED = 4000


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AgentProcessCase(unittest.IsolatedAsyncioTestCase):
    extra_config = ""

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        config_dir = self.home / ".config" / "agent"
        config_dir.mkdir(parents=True)
        self.port = free_port()
        (config_dir / "config.toml").write_text(
            f"port = {self.port}\nhandshake_timeout = 1.0\n{self.extra_config}")
        self.secret = secrets.token_bytes(32)
        env_file = config_dir / ".env"
        env_file.write_text(f"API_SECRET={self.secret.hex()}\n")
        env_file.chmod(0o600)
        self.proc = subprocess.Popen([sys.executable, str(AGENT_DIR / "agent.py")],
                                     env={**os.environ, "HOME": str(self.home)},
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await self.wait_for_event("listening")

    async def asyncTearDown(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            self.proc.wait(timeout=10)
        self._tmp.cleanup()

    def journal(self):
        path = self.home / ".local" / "state" / "agent" / "journal.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def events(self):
        return [r["event"] for r in self.journal()]

    async def wait_for_event(self, event, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if event in self.events():
                return
            await asyncio.sleep(0.05)
        self.fail(f"{event!r} not in journal: {self.events()}")

    async def bridge(self, secret=None) -> FakeBridge:
        b = FakeBridge(secret or self.secret, self.port)
        await b.connect()
        return b

    async def assert_closed_by_server(self, ws, code=CLOSE_POLICY, timeout=3.0):
        await asyncio.wait_for(ws.wait_closed(), timeout)
        self.assertEqual(ws.close_code, code)


class HandshakeTests(AgentProcessCase):
    async def test_round_trip(self):
        b = await self.bridge()
        token = await b.send_incoming("Ahmed", "مرحبا")
        reply = await b.recv()
        self.assertEqual(reply["type"], "propose_reply")
        self.assertEqual(reply["reply_token"], token)
        self.assertTrue(reply["proposed_text"])
        await b.close()

    async def test_server_with_wrong_secret_is_rejected_before_any_data(self):
        b = FakeBridge(secrets.token_bytes(32), self.port)  # agent holds a different secret
        with self.assertRaises(protocol.ProtocolError):
            await b.connect()
        await asyncio.sleep(0.3)
        self.assertNotIn("session_opened", self.events())
        self.assertNotIn("incoming", self.events())

    async def test_wrong_client_secret_is_closed(self):
        ws = await connect(f"ws://127.0.0.1:{self.port}")
        nonce_b = protocol.new_nonce()
        await ws.send(protocol.hello_frame(nonce_b))
        frame = protocol.parse_frame(await ws.recv())
        nonce_s = bytes.fromhex(frame["nonce_s"])
        await ws.send(protocol.auth_frame(secrets.token_bytes(32), nonce_b, nonce_s))
        await self.assert_closed_by_server(ws)
        self.assertIn("handshake_failed", self.events())
        self.assertNotIn("session_opened", self.events())

    async def test_message_before_handshake_is_closed(self):
        ws = await connect(f"ws://127.0.0.1:{self.port}")
        await ws.send(json.dumps({"type": "incoming", "event_id": "e", "reply_token": "t",
                                  "sender_name": "x", "text": "y"}))
        await self.assert_closed_by_server(ws)
        self.assertNotIn("incoming", self.events())

    async def test_silent_peer_times_out(self):
        ws = await connect(f"ws://127.0.0.1:{self.port}")
        start = time.monotonic()
        await self.assert_closed_by_server(ws, timeout=4.0)
        self.assertLess(time.monotonic() - start, 3.0)  # handshake_timeout = 1.0

    async def test_new_session_supersedes_old(self):
        first = await self.bridge()
        second = await self.bridge()
        await self.assert_closed_by_server(first.ws, code=CLOSE_SUPERSEDED)
        token = await second.send_incoming("Sara", "hi")
        self.assertEqual((await second.recv())["reply_token"], token)
        await second.close()


class ImpostorTests(unittest.IsolatedAsyncioTestCase):
    """mvp-1-spec 6: an impostor on the port must receive neither the secret nor content."""

    async def test_impostor_gets_nothing_but_hello(self):
        secret = secrets.token_bytes(32)
        recorded = []

        async def impostor(ws):
            async for raw in ws:
                recorded.append(raw)
                if len(recorded) == 1:  # answer hello with a challenge it cannot prove
                    await ws.send(json.dumps({"type": "challenge", "v": 1,
                                              "nonce_s": secrets.token_hex(16),
                                              "proof_s": secrets.token_hex(32)}))

        async with serve(impostor, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            bridge = FakeBridge(secret, port)
            with self.assertRaises(protocol.ProtocolError):
                await bridge.connect()
            await asyncio.sleep(0.3)

        self.assertEqual(len(recorded), 1)
        self.assertEqual(json.loads(recorded[0])["type"], "hello")
        everything = "".join(recorded)
        self.assertNotIn(secret.hex(), everything)
        self.assertNotIn("incoming", everything)


class ChannelTests(AgentProcessCase):
    async def test_bad_mac_closes(self):
        b = await self.bridge()
        frame = json.loads(b.channel.seal({"type": "proposal_ignored", "reply_token": "t"}))
        frame["mac"] = "00" * 32
        await b.ws.send(json.dumps(frame))
        await self.assert_closed_by_server(b.ws)
        self.assertIn("protocol_violation", self.events())

    async def test_replayed_frame_closes(self):
        b = await self.bridge()
        raw = b.channel.seal({"type": "proposal_ignored", "reply_token": "t"})
        await b.ws.send(raw)
        await b.ws.send(raw)  # same seq again
        await self.assert_closed_by_server(b.ws)

    async def test_reflected_server_frame_closes(self):
        b = await self.bridge()
        await b.send_incoming("Ahmed", "hi")
        raw = await b.ws.recv()  # a genuine s2c frame
        b.channel.open(raw)
        await b.ws.send(raw)  # replay it towards the server: wrong direction label
        await self.assert_closed_by_server(b.ws)

    async def test_invalid_fields_close(self):
        b = await self.bridge()
        await b.send({"type": "incoming", "event_id": "e1", "reply_token": "t1",
                      "sender_name": "x"})  # no text
        await self.assert_closed_by_server(b.ws)
        self.assertNotIn("proposal_sent", self.events())


class FlowTests(AgentProcessCase):
    async def test_twenty_senders_without_duplicates(self):
        b = await self.bridge()
        tokens = {}
        for i in range(20):
            event_id = f"evt-{i}"
            tokens[event_id] = await b.send_incoming(f"Sender {i}", f"message {i}", event_id=event_id)
        for i in range(5):  # WhatsApp re-posts the same notification
            await b.send_incoming(f"Sender {i}", f"message {i}", event_id=f"evt-{i}",
                                  reply_token=tokens[f"evt-{i}"])
        replies = [await b.recv() for _ in range(20)]
        self.assertEqual({r["reply_token"] for r in replies}, set(tokens.values()))
        with self.assertRaises(TimeoutError):
            await b.recv(timeout=1.0)  # no 21st proposal
        self.assertEqual(self.events().count("incoming_duplicate"), 5)
        await b.close()

    async def test_feedback_messages_are_recorded(self):
        b = await self.bridge()
        token = await b.send_incoming("Ahmed", "hi")
        await b.recv()
        await b.send({"type": "notification_sent", "reply_token": token, "sent_text": "ok"})
        await b.send({"type": "notification_expired", "reply_token": token})
        await self.wait_for_event("notification_expired")
        self.assertIn("notification_sent", self.events())
        await b.close()

    async def test_message_text_not_written_to_journal(self):
        b = await self.bridge()
        await b.send_incoming("Ahmed", "SECRET-CONTENT-123")
        await b.recv()
        await b.close()
        self.assertNotIn("SECRET-CONTENT-123", json.dumps(self.journal(), ensure_ascii=False))


class SlowReplyTests(AgentProcessCase):
    extra_config = "dummy_reply_delay = 1.0\n"

    async def test_slow_reply_does_not_block_other_senders(self):
        b = await self.bridge()
        start = time.monotonic()
        for i in range(5):
            await b.send_incoming(f"Sender {i}", "hi")
        replies = [await b.recv(timeout=5) for _ in range(5)]
        self.assertEqual(len(replies), 5)
        self.assertLess(time.monotonic() - start, 2.5)  # concurrent, not 5 x 1s
        await b.close()


if __name__ == "__main__":
    unittest.main()
