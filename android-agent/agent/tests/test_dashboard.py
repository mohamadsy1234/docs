"""Dashboard tests: access control, live state, emergency pause.

Run with: python3 -m unittest discover -s android-agent/agent/tests -v
"""

import http.client
import json
import os
import socket
import stat
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sprint1 import AgentProcessCase, free_port  # noqa: E402


class DashboardCase(AgentProcessCase):
    async def asyncSetUp(self):
        self.dash_port = free_port()
        self.extra_config = f"dashboard_port = {self.dash_port}\n"
        await super().asyncSetUp()
        await self.wait_for_event("dashboard_listening")
        url = (self.home / ".local/state/agent/dashboard.url").read_text().strip()
        self.token = urlsplit(url).query.split("=", 1)[1]

    def request(self, path, method="GET", token=True, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.dash_port, timeout=5)
        q = f"?token={self.token}" if token is True else (f"?token={token}" if token else "")
        headers = {"Host": host} if host else {}
        conn.request(method, path + q, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp, body

    def state(self):
        resp, body = self.request("/api/state")
        self.assertEqual(resp.status, 200)
        return json.loads(body)


class AccessTests(DashboardCase):
    async def test_url_file_is_private(self):
        mode = (self.home / ".local/state/agent/dashboard.url").stat().st_mode
        self.assertEqual(stat.S_IMODE(mode), 0o600)

    async def test_requires_token(self):
        self.assertEqual(self.request("/", token=None)[0].status, 403)
        self.assertEqual(self.request("/api/state", token="wrong")[0].status, 403)

    async def test_rejects_foreign_host_header(self):
        # A web page using DNS rebinding arrives with its own Host header.
        self.assertEqual(self.request("/api/state", host="evil.example:8001")[0].status, 403)

    async def test_page_has_strict_csp(self):
        resp, body = self.request("/")
        self.assertEqual(resp.status, 200)
        csp = resp.getheader("Content-Security-Policy")
        self.assertIn("default-src 'none'", csp)
        self.assertNotIn("unsafe-inline", csp)
        nonce = csp.split("'nonce-")[1].split("'")[0]
        self.assertIn(f'nonce="{nonce}"'.encode(), body)
        self.assertNotIn(b"__NONCE__", body)

    async def test_page_has_no_remote_assets(self):
        _, body = self.request("/")
        for marker in (b"cdn.", b"googleapis", b"<link rel=\"stylesheet\"", b"src=\"http"):
            self.assertNotIn(marker, body)

    async def test_no_send_endpoint(self):
        for path in ("/api/send", "/api/approve"):
            self.assertEqual(self.request(path, method="POST")[0].status, 404)

    async def test_bad_request_is_contained(self):
        with socket.create_connection(("127.0.0.1", self.dash_port), timeout=5) as s:
            s.sendall(b"GARBAGE\r\n\r\n")
            self.assertIn(b"400", s.recv(1024))


class StateTests(DashboardCase):
    async def test_live_state_follows_bridge_traffic(self):
        s = self.state()
        self.assertFalse(s["bridge"]["connected"])
        b = await self.bridge()
        token = await b.send_incoming("Ahmed <img src=x onerror=alert(1)>", "مرحبا")
        await b.recv()
        s = self.state()
        self.assertTrue(s["bridge"]["connected"])
        self.assertEqual(s["counters"]["incoming"], 1)
        self.assertEqual(s["counters"]["proposals"], 1)
        pending = s["pending"][0]
        # Sender names are attacker-controlled: returned as data, rendered with textContent.
        self.assertEqual(pending["sender"], "Ahmed <img src=x onerror=alert(1)>")
        self.assertTrue(pending["proposal"])
        self.assertEqual(len(s["latency_ms"]), 1)

        await b.send({"type": "notification_sent", "reply_token": token, "sent_text": "ok"})
        await self.wait_for_event("notification_sent")
        s = self.state()
        self.assertEqual(s["pending"], [])
        self.assertEqual(s["counters"]["sent"], 1)
        await b.close()

    async def test_events_never_contain_message_text(self):
        b = await self.bridge()
        await b.send_incoming("Ahmed", "PRIVATE-TEXT-42")
        await b.recv()
        await b.close()
        self.assertNotIn("PRIVATE-TEXT-42", json.dumps(self.state()["events"], ensure_ascii=False))


class PauseTests(DashboardCase):
    async def test_pause_withholds_proposals_and_survives_restart(self):
        resp, body = self.request("/api/pause", method="POST")
        self.assertEqual(json.loads(body), {"paused": True})
        self.assertEqual(self.request("/api/pause", method="GET")[0].status, 404)  # POST only

        b = await self.bridge()
        await b.send_incoming("Ahmed", "hi")
        with self.assertRaises(TimeoutError):
            await b.recv(timeout=1.0)
        await b.close()
        self.assertIn("proposal_withheld", self.events())

        # Restart: the pause must still hold.
        self.proc.terminate()
        self.proc.wait(timeout=10)
        await self.asyncSetUp_restart()
        b = await self.bridge()
        await b.send_incoming("Ahmed", "again")
        with self.assertRaises(TimeoutError):
            await b.recv(timeout=1.0)

        self.request("/api/resume", method="POST")
        await b.send_incoming("Ahmed", "after resume")
        self.assertEqual((await b.recv())["type"], "propose_reply")
        await b.close()

    async def asyncSetUp_restart(self):
        import subprocess
        self.proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent.parent / "agent.py")],
                                     env={**os.environ, "HOME": str(self.home)},
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        before = self.events().count("dashboard_listening")
        import asyncio, time
        deadline = time.monotonic() + 8
        while self.events().count("dashboard_listening") <= before and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        url = (self.home / ".local/state/agent/dashboard.url").read_text().strip()
        self.token = urlsplit(url).query.split("=", 1)[1]


if __name__ == "__main__":
    unittest.main()
