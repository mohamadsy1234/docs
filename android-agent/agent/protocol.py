"""Bridge <-> agent wire protocol (mvp-1-spec.md, section 2).

Pure functions and a channel class, shared by the agent (server side) and
the fake Bridge used in tests (client side). The exact byte layout here is
the contract the Kotlin client must reproduce; see mvp-1-spec.md 2.2.1.

Handshake (all frames are JSON text):
  C -> S  {"type": "hello",     "v": 1, "nonce_b": hex16}
  S -> C  {"type": "challenge", "v": 1, "nonce_s": hex16, "proof_s": hex32}
  C -> S  {"type": "auth",      "proof_b": hex32}
  proof_s = HMAC(secret, b"server" + nonce_b + nonce_s)
  proof_b = HMAC(secret, b"client" + nonce_s + nonce_b)
  K_s     = HMAC(secret, b"session" + nonce_b + nonce_s)

After the handshake every frame is an envelope:
  {"seq": n, "body": "<JSON text of the message>", "mac": hex32}
  mac = HMAC(K_s, direction + seq.to_bytes(8, "big") + body.encode("utf-8"))
  direction is b"c2s" or b"s2c"; seq starts at 1 per direction and must be
  exactly last + 1. The MAC covers the body string as sent, so neither side
  ever re-serialises JSON before verifying.
"""

import hashlib
import hmac
import json
import secrets
from typing import Any, Dict, Tuple

VERSION = 1
NONCE_BYTES = 16
C2S = b"c2s"
S2C = b"s2c"


class ProtocolError(Exception):
    """Any violation: the connection must be closed."""


def _hmac(key: bytes, *parts: bytes) -> bytes:
    return hmac.new(key, b"".join(parts), hashlib.sha256).digest()


def new_nonce() -> bytes:
    return secrets.token_bytes(NONCE_BYTES)


def server_proof(secret: bytes, nonce_b: bytes, nonce_s: bytes) -> bytes:
    return _hmac(secret, b"server", nonce_b, nonce_s)


def client_proof(secret: bytes, nonce_b: bytes, nonce_s: bytes) -> bytes:
    return _hmac(secret, b"client", nonce_s, nonce_b)


def session_key(secret: bytes, nonce_b: bytes, nonce_s: bytes) -> bytes:
    return _hmac(secret, b"session", nonce_b, nonce_s)


def parse_frame(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, str):
        raise ProtocolError("binary frames are not allowed")
    try:
        frame = json.loads(raw)
    except ValueError:
        raise ProtocolError("frame is not JSON")
    if not isinstance(frame, dict):
        raise ProtocolError("frame is not a JSON object")
    return frame


def hex_field(frame: Dict[str, Any], name: str, length: int) -> bytes:
    value = frame.get(name)
    if not isinstance(value, str) or len(value) != 2 * length:
        raise ProtocolError(f"{name} must be {length} bytes of hex")
    try:
        return bytes.fromhex(value)
    except ValueError:
        raise ProtocolError(f"{name} is not hex")


def hello_frame(nonce_b: bytes) -> str:
    return json.dumps({"type": "hello", "v": VERSION, "nonce_b": nonce_b.hex()})


def read_hello(raw: Any) -> bytes:
    frame = parse_frame(raw)
    if frame.get("type") != "hello" or frame.get("v") != VERSION:
        raise ProtocolError("expected hello v1")
    return hex_field(frame, "nonce_b", NONCE_BYTES)


def challenge_frame(secret: bytes, nonce_b: bytes) -> Tuple[str, bytes]:
    nonce_s = new_nonce()
    frame = {"type": "challenge", "v": VERSION, "nonce_s": nonce_s.hex(),
             "proof_s": server_proof(secret, nonce_b, nonce_s).hex()}
    return json.dumps(frame), nonce_s


def read_challenge(raw: Any, secret: bytes, nonce_b: bytes) -> bytes:
    """Client side: verify the server before sending anything else."""
    frame = parse_frame(raw)
    if frame.get("type") != "challenge" or frame.get("v") != VERSION:
        raise ProtocolError("expected challenge v1")
    nonce_s = hex_field(frame, "nonce_s", NONCE_BYTES)
    proof_s = hex_field(frame, "proof_s", 32)
    if not hmac.compare_digest(proof_s, server_proof(secret, nonce_b, nonce_s)):
        raise ProtocolError("server proof invalid")
    return nonce_s


def auth_frame(secret: bytes, nonce_b: bytes, nonce_s: bytes) -> str:
    return json.dumps({"type": "auth", "proof_b": client_proof(secret, nonce_b, nonce_s).hex()})


def read_auth(raw: Any, secret: bytes, nonce_b: bytes, nonce_s: bytes) -> None:
    frame = parse_frame(raw)
    if frame.get("type") != "auth":
        raise ProtocolError("expected auth")
    proof_b = hex_field(frame, "proof_b", 32)
    if not hmac.compare_digest(proof_b, client_proof(secret, nonce_b, nonce_s)):
        raise ProtocolError("client proof invalid")


class Channel:
    """Seals outgoing and opens incoming envelopes for one direction pair."""

    def __init__(self, key: bytes, send_dir: bytes, recv_dir: bytes):
        self._key = key
        self._send_dir = send_dir
        self._recv_dir = recv_dir
        self._send_seq = 0
        self._recv_seq = 0

    def _mac(self, direction: bytes, seq: int, body: str) -> bytes:
        return _hmac(self._key, direction, seq.to_bytes(8, "big"), body.encode("utf-8"))

    def seal(self, message: Dict[str, Any]) -> str:
        self._send_seq += 1
        body = json.dumps(message, ensure_ascii=False)
        mac = self._mac(self._send_dir, self._send_seq, body)
        return json.dumps({"seq": self._send_seq, "body": body, "mac": mac.hex()}, ensure_ascii=False)

    def open(self, raw: Any) -> Dict[str, Any]:
        frame = parse_frame(raw)
        seq, body = frame.get("seq"), frame.get("body")
        if not isinstance(seq, int) or isinstance(seq, bool) or not isinstance(body, str):
            raise ProtocolError("malformed envelope")
        mac = hex_field(frame, "mac", 32)
        # Check the MAC before trusting seq, so a forged seq cannot desync us.
        if not hmac.compare_digest(mac, self._mac(self._recv_dir, seq, body)):
            raise ProtocolError("bad mac")
        if seq != self._recv_seq + 1:
            raise ProtocolError(f"seq {seq} after {self._recv_seq}")
        self._recv_seq = seq
        message = parse_frame(body)
        if not isinstance(message.get("type"), str):
            raise ProtocolError("message without type")
        return message


def server_channel(key: bytes) -> Channel:
    return Channel(key, send_dir=S2C, recv_dir=C2S)


def client_channel(key: bytes) -> Channel:
    return Channel(key, send_dir=C2S, recv_dir=S2C)
