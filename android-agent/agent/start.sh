#!/usr/bin/env sh
# Start the agent in this Termux session and open its private dashboard.
# Usage: sh start.sh
set -e
cd "$(dirname "$0")"

if ! python3 agent.py --check; then
    echo
    echo "Pre-flight check failed (see the false line above). Setup: ../START.md"
    exit 1
fi

if grep -q '^ANTHROPIC_API_KEY=.' "$HOME/.config/agent/.env" 2>/dev/null && python3 -c 'import anthropic' 2>/dev/null; then
    echo "Replies: Claude"
else
    echo "Replies: fixed test text (add ANTHROPIC_API_KEY and 'pip install anthropic' for Claude, see ../START.md)"
fi

# Keep the CPU awake while the agent runs (needs the Termux:API app).
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock

# Open the dashboard once the agent has written its private link.
(
    i=0
    while [ $i -lt 50 ]; do
        if url=$(python3 agent.py --dashboard-url 2>/dev/null); then
            if command -v termux-open-url >/dev/null 2>&1; then
                termux-open-url "$url"
            else
                echo "Dashboard: $url"
            fi
            exit 0
        fi
        i=$((i + 1))
        sleep 0.2
    done
) &

exec python3 agent.py
