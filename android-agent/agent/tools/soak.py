#!/usr/bin/env python3
"""Soak test for the Sprint 0 agent on a real device (see TERMUX-RUNBOOK.md).

Starts agent.py, samples its /proc stats into a CSV, then stops it with
SIGTERM and checks the Sprint 0 gate items that need a long run:
survival, memory growth, warnings on stderr, journal health, clean exit.

Usage: python3 soak.py --minutes 30 --label B-screen-off
"""

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent.py"
STATE = Path.home() / ".local" / "state" / "agent"
WARNING_MARKERS = ("Warning", "Task was destroyed", "Traceback", "never awaited")


def proc_sample(pid: int) -> dict:
    status = Path(f"/proc/{pid}/status").read_text()
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    ticks = os.sysconf("SC_CLK_TCK")
    return {
        "rss_kb": int(fields["VmRSS"].split()[0]),
        "threads": int(fields["Threads"]),
        "fds": len(os.listdir(f"/proc/{pid}/fd")),
        "cpu_s": round((int(stat[11]) + int(stat[12])) / ticks, 2),
    }


def journal_events(since: float) -> list:
    path = STATE / "journal.jsonl"
    if not path.exists():
        return []
    records = [json.loads(line) for line in path.read_text().splitlines()]  # raises if not JSONL
    return [r for r in records if r["ts"] >= since]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--interval", type=float, default=30, help="seconds between samples")
    ap.add_argument("--label", default="run", help="scenario name, e.g. A-foreground")
    args = ap.parse_args()

    STATE.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    csv_path = STATE / f"soak-{args.label}-{stamp}.csv"
    err_path = STATE / f"soak-{args.label}-{stamp}.stderr"

    started_wall = time.time()
    with err_path.open("w") as err:
        agent = subprocess.Popen([sys.executable, "-W", "default", str(AGENT)],
                                 stdout=subprocess.DEVNULL, stderr=err)
    # Sample only once the agent is fully up, or start-up allocation looks like growth.
    ready_by = time.monotonic() + 15
    while not any(e["event"] == "listening" for e in journal_events(started_wall)):
        if agent.poll() is not None or time.monotonic() > ready_by:
            print(f"agent did not start (exit code {agent.poll()}); see {err_path}")
            return 1
        time.sleep(0.2)
    print(f"agent pid {agent.pid}; sampling every {args.interval}s for {args.minutes} min")
    print(f"csv: {csv_path}")

    deadline = time.monotonic() + args.minutes * 60
    samples = []
    with csv_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["wall_time", "elapsed_s", "rss_kb", "threads", "fds", "cpu_s"])
        while time.monotonic() < deadline and agent.poll() is None:
            try:
                s = proc_sample(agent.pid)
            except (FileNotFoundError, ProcessLookupError):
                break
            samples.append(s)
            writer.writerow([time.strftime("%H:%M:%S"), round(time.time() - started_wall),
                             s["rss_kb"], s["threads"], s["fds"], s["cpu_s"]])
            f.flush()
            time.sleep(args.interval)

    report = {"label": args.label, "planned_minutes": args.minutes,
              "survived_minutes": round((time.time() - started_wall) / 60, 1)}

    if agent.poll() is None:
        t0 = time.monotonic()
        agent.send_signal(signal.SIGTERM)
        try:
            code = agent.wait(timeout=10)
        except subprocess.TimeoutExpired:
            agent.kill()
            code = agent.wait()
        report["survived"] = True
        report["sigterm_exit_code"] = code
        report["sigterm_seconds"] = round(time.monotonic() - t0, 2)
    else:
        code = agent.returncode
        report["survived"] = False
        # Negative = killed by a signal; -9 from outside is the phantom process
        # killer or the low-memory killer (no journal line can explain it).
        report["death"] = (f"killed by {signal.Signals(-code).name}" if code < 0
                           else f"exited with code {code}")

    events = journal_events(started_wall)
    names = [e["event"] for e in events]
    report["journal"] = {k: names.count(k) for k in
                         ("HANG", "crashed", "process_resumed", "shutdown", "port_bind_failed")}
    stderr_text = err_path.read_text()
    report["stderr_warnings"] = sum(stderr_text.count(m) for m in WARNING_MARKERS)

    if samples:
        # Skip the first 5 minutes as warm-up when the run is long enough.
        warm = [s for i, s in enumerate(samples) if i * args.interval >= 300] or samples
        report["rss_kb"] = {"start": samples[0]["rss_kb"], "after_warmup": warm[0]["rss_kb"],
                            "end": samples[-1]["rss_kb"], "max": max(s["rss_kb"] for s in samples)}
        report["rss_growth_after_warmup_kb"] = samples[-1]["rss_kb"] - warm[0]["rss_kb"]
        report["threads_max"] = max(s["threads"] for s in samples)
        report["fds"] = {"start": samples[0]["fds"], "end": samples[-1]["fds"]}
        report["cpu_seconds_total"] = samples[-1]["cpu_s"]

    gate = (report["survived"] and report.get("sigterm_exit_code") == 0
            and report.get("sigterm_seconds", 99) < 6 and report["journal"]["HANG"] == 0
            and report["journal"]["crashed"] == 0 and report["stderr_warnings"] == 0
            and report.get("rss_growth_after_warmup_kb", 0) <= 2048)
    report["gate_pass"] = gate

    print(json.dumps(report, indent=2))
    (STATE / f"soak-{args.label}-{stamp}.json").write_text(json.dumps(report, indent=2))
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
