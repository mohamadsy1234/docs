"""Proposal engine tests (replier.py).

The Claude path runs the real agent process and the real `anthropic` SDK
against a local fake of the Messages API (ANTHROPIC_BASE_URL), so no key or
network is needed. Skipped when the SDK is not installed.
Run with: python3 -m unittest discover -s android-agent/agent/tests -v
"""

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

AGENT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import replier  # noqa: E402
from test_sprint1 import AgentProcessCase  # noqa: E402

try:
    import anthropic  # noqa: F401
    HAVE_SDK = True
except ImportError:
    HAVE_SDK = False

CONFIG = {"llm_model": "claude-opus-5-5", "llm_effort": "low", "llm_timeout": 5.0, "llm_persona": ""}


class FakeMessagesApi:
    """Answers POST /v1/messages with a canned reply and records each request."""

    def __init__(self):
        self.requests = []
        self.status = 200
        self.reply = "أهلاً، تمام وأنت؟"
        self.stop_reason = "end_turn"

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def handle(self, reader, writer):
        try:
            while True:
                request_line = await reader.readline()
                if not request_line:
                    break
                headers = {}
                while (line := await reader.readline()) not in (b"\r\n", b""):
                    name, _, value = line.decode().partition(":")
                    headers[name.strip().lower()] = value.strip()
                body = await reader.readexactly(int(headers.get("content-length", 0)))
                self.requests.append({"path": request_line.split()[1].decode(),
                                      "headers": headers, "body": json.loads(body)})
                if self.status == 200:
                    payload = {"id": "msg_test", "type": "message", "role": "assistant",
                               "model": "claude-opus-5-5", "stop_reason": self.stop_reason,
                               "stop_sequence": None,
                               "content": [{"type": "text", "text": self.reply}],
                               "usage": {"input_tokens": 10, "output_tokens": 5}}
                else:
                    payload = {"type": "error",
                               "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
                data = json.dumps(payload).encode()
                writer.write(f"HTTP/1.1 {self.status} X\r\nContent-Type: application/json\r\n"
                             f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


class FakeClient:
    """Stands in for AsyncAnthropic in unit tests."""

    def __init__(self, response):
        self.calls = []
        self._response = response
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response

    async def close(self):
        pass


def response(text="تمام", stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason,
                           content=[SimpleNamespace(type="text", text=text)])


class MakeReplierTests(unittest.TestCase):
    def test_no_key_uses_dummy(self):
        self.assertEqual(replier.make_replier(CONFIG, None).name, "dummy")
        self.assertEqual(replier.make_replier(CONFIG, "").name, "dummy")

    @unittest.skipUnless(HAVE_SDK, "anthropic not installed")
    def test_key_uses_claude(self):
        self.assertEqual(replier.make_replier(CONFIG, "sk-test").name, "claude")


@unittest.skipUnless(HAVE_SDK, "anthropic not installed")
class ClaudeReplierTests(unittest.IsolatedAsyncioTestCase):
    def make(self, resp, **config):
        r = replier.ClaudeReplier("sk-test", {**CONFIG, **config})
        r._client = FakeClient(resp)
        return r

    async def test_request_shape(self):
        r = self.make(response(" تمام \n"))
        self.assertEqual(await r.propose("Ahmed", "كيفك؟"), "تمام")
        call = r._client.calls[0]
        self.assertEqual(call["model"], "claude-opus-5-5")
        self.assertEqual(call["output_config"], {"effort": "low"})
        self.assertEqual(call["fallbacks"], "default")
        self.assertEqual(call["betas"], ["server-side-fallback-2026-07-01"])
        self.assertIn("كيفك؟", call["messages"][0]["content"])
        self.assertIn("Ahmed", call["messages"][0]["content"])
        self.assertNotIn("thinking", call)  # thinking stays adaptive; effort controls depth

    async def test_persona_is_appended_to_system_prompt(self):
        r = self.make(response(), llm_persona="I write short, informal Syrian Arabic.")
        await r.propose("A", "hi")
        system = r._client.calls[0]["system"]
        self.assertTrue(system.startswith(replier.SYSTEM_PROMPT))
        self.assertIn("Syrian Arabic", system)

    async def test_unusable_responses_raise(self):
        cases = [(response(stop_reason="refusal"), "llm_refused"),
                 (response(stop_reason="max_tokens"), "llm_truncated"),
                 (response("   "), "llm_empty"),
                 (response("x" * (replier.MAX_REPLY + 1)), "llm_too_long")]
        for resp, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(replier.ReplyError) as ctx:
                    await self.make(resp).propose("A", "hi")
                self.assertEqual(str(ctx.exception), reason)


@unittest.skipUnless(HAVE_SDK, "anthropic not installed")
class ClaudeEndToEndTests(AgentProcessCase):
    """Real agent + real SDK + fake Messages API."""

    extra_env_lines = "ANTHROPIC_API_KEY=sk-test-key\n"

    async def asyncSetUp(self):
        self.api = FakeMessagesApi()
        await self.api.start()
        await super().asyncSetUp()

    async def asyncTearDown(self):
        await super().asyncTearDown()
        await self.api.stop()

    def process_env(self):
        return {"ANTHROPIC_BASE_URL": self.api.url}

    async def test_proposal_comes_from_claude(self):
        brain = [r for r in self.journal() if r["event"] == "brain"]
        self.assertEqual(brain[-1]["engine"], "claude")
        b = await self.bridge()
        token = await b.send_incoming("Ahmed", "كيف حالك؟")
        reply = await asyncio.wait_for(b.recv(), 10)
        self.assertEqual(reply, {"type": "propose_reply", "reply_token": token,
                                 "proposed_text": self.api.reply})
        req = self.api.requests[0]
        self.assertEqual(req["path"], "/v1/messages?beta=true")
        self.assertEqual(req["headers"]["x-api-key"], "sk-test-key")
        self.assertIn("server-side-fallback-2026-07-01", req["headers"]["anthropic-beta"])
        self.assertIn("كيف حالك؟", req["body"]["messages"][0]["content"])
        await b.close()
        # The message text and the reply never reach the journal.
        journal = "\n".join(json.dumps(r, ensure_ascii=False) for r in self.journal())
        self.assertNotIn("كيف حالك", journal)
        self.assertNotIn(self.api.reply, journal)

    async def test_api_error_means_no_proposal_and_session_survives(self):
        self.api.status = 401
        b = await self.bridge()
        await b.send_incoming("Ahmed", "hi")
        await self.wait_for_event("proposal_failed", timeout=10)
        failed = [r for r in self.journal() if r["event"] == "proposal_failed"]
        self.assertEqual(failed[-1]["reason"], "llm_auth_failed")
        # The session is still up: the next message gets a proposal once the API recovers.
        self.api.status = 200
        token = await b.send_incoming("Ahmed", "hi again")
        reply = await asyncio.wait_for(b.recv(), 10)
        self.assertEqual(reply["reply_token"], token)
        await b.close()


if __name__ == "__main__":
    unittest.main()
