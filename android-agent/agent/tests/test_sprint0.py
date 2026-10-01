"""Sprint 0 acceptance tests (Section 1.2, 1.4, 1.5, 1.6).

Each test runs agent.py as a real process with HOME pointed at a temp dir.
Run with: python3 -m unittest discover -s android-agent/agent/tests -v
"""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent.py"
SECRET = "ab" * 32


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.config_dir = self.home / ".config" / "agent"
        self.state_dir = self.home / ".local" / "state" / "agent"
        self.config_dir.mkdir(parents=True)
        self.port = free_port()
        self.write_config(f"port = {self.port}\n")
        self.write_secret(f"API_SECRET={SECRET}\n")
        self.env = {**os.environ, "HOME": str(self.home)}

    def tearDown(self):
        self._tmp.cleanup()

    def write_config(self, text: str) -> None:
        (self.config_dir / "config.toml").write_text(text)

    def write_secret(self, text: str, mode: int = 0o600) -> None:
        path = self.config_dir / ".env"
        path.write_text(text)
        path.chmod(mode)

    def run_agent(self, *args, timeout=10):
        return subprocess.run([sys.executable, str(AGENT), *args], env=self.env,
                              capture_output=True, text=True, timeout=timeout)

    def start_agent(self, extra_env=None):
        env = {**self.env, **(extra_env or {})}
        return subprocess.Popen([sys.executable, str(AGENT)], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def journal(self):
        path = self.state_dir / "journal.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def events(self):
        return [r["event"] for r in self.journal()]

    def wait_for_event(self, event, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if event in self.events():
                return
            time.sleep(0.05)
        self.fail(f"{event!r} not in journal: {self.events()}")


class CheckTests(AgentTestCase):
    def test_all_pass(self):
        r = self.run_agent("--check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(r.stdout.splitlines(),
                         ["python_ok=true", "secret_ok=true", "sqlite_ok=true", "port_free=true"])

    def test_missing_secret_fails(self):
        (self.config_dir / ".env").unlink()
        r = self.run_agent("--check")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("secret_ok=false", r.stdout)

    def test_port_taken_fails(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", self.port))
            s.listen()
            r = self.run_agent("--check")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("port_free=false", r.stdout)

    def test_no_side_effects(self):
        self.run_agent("--check")
        self.assertFalse((self.state_dir / "memory.db").exists())
        self.assertFalse((self.state_dir / "journal.jsonl").exists())


class LifecycleTests(AgentTestCase):
    def test_sigterm_clean_exit(self):
        p = self.start_agent()
        self.wait_for_event("listening")
        time.sleep(1.0)  # no Bridge connected: the agent must keep running
        self.assertIsNone(p.poll())
        start = time.monotonic()
        p.send_signal(signal.SIGTERM)
        code = p.wait(timeout=10)
        self.assertEqual(code, 0)
        self.assertLess(time.monotonic() - start, 6.0)
        self.assertIn("shutdown", self.events())

    def test_sigint_exit_130(self):
        p = self.start_agent()
        self.wait_for_event("listening")
        p.send_signal(signal.SIGINT)
        self.assertEqual(p.wait(timeout=10), 130)

    def test_bad_secret_exit_3_before_bind(self):
        self.write_secret("API_SECRET=not-hex\n")
        r = self.run_agent()
        self.assertEqual(r.returncode, 3)
        self.assertNotIn("listening", self.events())

    def test_loose_secret_permissions_exit_3(self):
        self.write_secret(f"API_SECRET={SECRET}\n", mode=0o644)
        self.assertEqual(self.run_agent().returncode, 3)

    def test_bad_config_exit_1(self):
        self.write_config("port = 'eight thousand'\n")
        self.assertEqual(self.run_agent().returncode, 1)

    def test_port_taken_exit_2_fast(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", self.port))
            s.listen()
            start = time.monotonic()
            r = self.run_agent()
            elapsed = time.monotonic() - start
        self.assertEqual(r.returncode, 2)
        self.assertLess(elapsed, 3.0)
        self.assertIn("port_bind_failed", self.events())

    def test_hang_exit_5(self):
        self.write_config(f"port = {self.port}\nheartbeat_interval = 0.5\nwatchdog_timeout = 2.0\n")
        p = self.start_agent({"AGENT_DEBUG_FREEZE_AFTER": "1"})
        self.assertEqual(p.wait(timeout=15), 5)
        self.assertIn("HANG", self.events())

    def test_journal_is_valid_jsonl(self):
        p = self.start_agent()
        self.wait_for_event("listening")
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)
        records = self.journal()  # json.loads on every line, raises if invalid
        self.assertTrue(all({"ts", "level", "event"} <= r.keys() for r in records))


if __name__ == "__main__":
    unittest.main()
