#!/usr/bin/env python3
"""
Python Agent Brain - Sprint 0 (Skeleton)
Handles process lifecycle, watchdog, CLI arguments, and environment checks.
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from typing import Dict, Any

# --- Constants & Exit Codes ---
EXIT_NORMAL = 0
EXIT_ERROR = 1
EXIT_PORT_BOUND = 2
EXIT_AUTH_DEP = 3
EXIT_HANG = 5

PORT = 8000
HOST = "127.0.0.1"
WATCHDOG_TIMEOUT = 10.0  # seconds
HEARTBEAT_INTERVAL = 2.0 # seconds

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("AgentBrain")

# --- Shared State ---
class AgentState:
    def __init__(self):
        self.last_heartbeat: float = time.time()
        self.is_running: bool = True
        self.lock = threading.Lock()

    def update_heartbeat(self):
        with self.lock:
            self.last_heartbeat = time.time()

    def get_last_heartbeat(self) -> float:
        with self.lock:
            return self.last_heartbeat

state = AgentState()

# --- Watchdog Thread ---
def watchdog_thread_func(state: AgentState):
    """Independent thread to detect asyncio event loop hangs."""
    logger.info("Watchdog thread started.")
    while state.is_running:
        time.sleep(1.0)
        time_since_heartbeat = time.time() - state.get_last_heartbeat()

        if time_since_heartbeat > WATCHDOG_TIMEOUT:
            logger.critical(f"WATCHDOG TRIGGERED: No heartbeat for {time_since_heartbeat:.1f}s. Event loop is hanging!")
            # Use os._exit to forcefully kill the process, bypassing normal shutdown,
            # ensuring the system knows it was a critical hang (Exit Code 5).
            os._exit(EXIT_HANG)

    logger.info("Watchdog thread stopped.")

# --- Environment Checker (--check) ---
def check_environment() -> Dict[str, Any]:
    """Runs pre-flight checks and returns a JSON serializable dict."""
    results = {
        "python_version": sys.version.split()[0],
        "port_8000_available": False,
        "env_file_exists": os.path.exists(".env"),
        "status": "pass"
    }

    # Check Port availability
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((HOST, PORT))
        results["port_8000_available"] = True
    except OSError:
        results["port_8000_available"] = False
        results["status"] = "fail"
        results["error"] = f"Port {PORT} is already bound by another process."
    finally:
        sock.close()

    # In a real scenario, we'd also check for required packages (e.g., websockets) here.
    return results

# --- Async Main Loop ---
async def agent_loop():
    """The main asyncio event loop (Network & Logic)."""
    logger.info("Agent async loop started.")
    try:
        while state.is_running:
            # Simulate work and emit heartbeat
            state.update_heartbeat()
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            # Future: WebSocket server will run here
    except asyncio.CancelledError:
        logger.info("Agent loop cancelled.")
    except Exception as e:
        logger.error(f"Agent loop error: {e}")
    finally:
        logger.info("Agent async loop finished.")

# --- Signal Handling ---
def handle_shutdown_signal(sig, frame):
    logger.info(f"Received shutdown signal ({sig}). Initiating graceful exit...")
    state.is_running = False

# --- Main Entry Point ---
def main():
    parser = argparse.ArgumentParser(description="Android Agent Python Brain")
    parser.add_argument("--check", action="store_true", help="Run pre-flight environment checks and output JSON")
    args = parser.parse_args()

    if args.check:
        results = check_environment()
        print(json.dumps(results, indent=2))
        if results["status"] == "pass":
            sys.exit(EXIT_NORMAL)
        elif not results["port_8000_available"]:
            sys.exit(EXIT_PORT_BOUND)
        else:
            sys.exit(EXIT_AUTH_DEP)

    # Prevent running if port is already squatted before we even start the server
    check_results = check_environment()
    if not check_results["port_8000_available"]:
        logger.error(f"FATAL: Port {PORT} is already in use. Potential port-squatting or zombie process.")
        sys.exit(EXIT_PORT_BOUND)

    # Register signals for graceful shutdown (Termux usually sends SIGINT or SIGTERM)
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)

    # Start independent Watchdog
    watchdog = threading.Thread(target=watchdog_thread_func, args=(state,), daemon=True)
    watchdog.start()

    # Start Async Event Loop
    try:
        asyncio.run(agent_loop())
    except KeyboardInterrupt:
        pass # Handled by signal
    finally:
        state.is_running = False
        watchdog.join(timeout=2.0)
        logger.info("Agent shut down successfully.")
        sys.exit(EXIT_NORMAL)

if __name__ == "__main__":
    main()
