#!/usr/bin/env python3
"""Fake Bridge client: the Bridge side of mvp-1-spec.md section 2, in Python.

Used by the Sprint 1 acceptance tests, and runnable by hand against a live
agent (e.g. on the phone) to see one full round trip:

    python3 tools/fake_bridge.py --port 8000

It reads API_SECRET from ~/.config/agent/.env like the agent does.
"""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from websockets.asyncio.client import ClientConnection, connect  # noqa: E402

import protocol  # noqa: E402


class FakeBridge:
    def __init__(self, secret: bytes, port: int):
        self.secret = secret
        self.port = port
        self.ws: Optional[ClientConnection] = None
        self.channel: Optional[protocol.Channel] = None

    async def connect(self) -> None:
        """Connect and run the client half of the mutual handshake.

        Raises ProtocolError if the server cannot prove it knows the secret;
        in that case nothing beyond the hello has been sent.
        """
        self.ws = await connect(f"ws://127.0.0.1:{self.port}", open_timeout=5)
        nonce_b = protocol.new_nonce()
        await self.ws.send(protocol.hello_frame(nonce_b))
        try:
            nonce_s = protocol.read_challenge(await self.ws.recv(), self.secret, nonce_b)
        except protocol.ProtocolError:
            await self.ws.close()
            raise
        await self.ws.send(protocol.auth_frame(self.secret, nonce_b, nonce_s))
        self.channel = protocol.client_channel(protocol.session_key(self.secret, nonce_b, nonce_s))

    async def send(self, message: Dict[str, Any]) -> None:
        await self.ws.send(self.channel.seal(message))

    async def recv(self, timeout: float = 5.0) -> Dict[str, Any]:
        async with asyncio.timeout(timeout):
            return self.channel.open(await self.ws.recv())

    async def send_incoming(self, sender: str, text: str, event_id: Optional[str] = None,
                            reply_token: Optional[str] = None) -> str:
        token = reply_token or f"rt_{uuid.uuid4().hex[:12]}"
        await self.send({"type": "incoming", "event_id": event_id or uuid.uuid4().hex,
                         "reply_token": token, "sender_name": sender, "text": text,
                         "posted_at": 0})
        return token

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()


def read_secret() -> bytes:
    env = Path.home() / ".config" / "agent" / ".env"
    for line in env.read_text().splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "API_SECRET":
            return bytes.fromhex(value.strip().strip("'\""))
    raise SystemExit(f"API_SECRET not found in {env}")


async def smoke(port: int) -> int:
    bridge = FakeBridge(read_secret(), port)
    await bridge.connect()
    print("handshake ok")
    token = await bridge.send_incoming("Test Sender", "مرحبا، هل أنت متاح غداً؟")
    reply = await bridge.recv(timeout=60)
    print(json.dumps(reply, ensure_ascii=False))
    ok = reply.get("type") == "propose_reply" and reply.get("reply_token") == token
    await bridge.send({"type": "proposal_ignored", "reply_token": token})
    await bridge.close()
    print("round trip ok" if ok else "unexpected reply")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    sys.exit(asyncio.run(smoke(ap.parse_args().port)))
