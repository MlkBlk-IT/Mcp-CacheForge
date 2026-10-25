#!/usr/bin/env bash
# Entry point for IcyCache – starts the server then runs the warm‑up script.
# The server is the same command you use today (uvicorn src.server:app …).
# After a short sleep we launch warmup_cache.py in background so the cache
# gets populated before any external request arrives.

# Launch server (foreground) – keep the process alive.
uvicorn src.server:app --host 127.0.0.1 --port 8080 --log-level warning &
SERVER_PID=$!

# Give the server a moment to listen on the port.
sleep 2

# Run warm‑up (non‑blocking, output goes to stderr).
python3 "$(dirname "$0")/warmup_cache.py" &
WARMUP_PID=$!

# Wait for both processes – if the server exits the script ends.
wait $SERVER_PID
wait $WARMUP_PID
