#!/usr/bin/env bash
# Start (or restart) the mock detached; PORT and FLAKE=0 optional. Resets mock state.
cd "$(dirname "$0")"; PORT="${PORT:-8787}"
[ -f .mock.pid ] && kill "$(cat .mock.pid)" 2>/dev/null; sleep 0.3
ARGS="--port $PORT"; [ "${FLAKE:-1}" = 0 ] && ARGS="$ARGS --no-flake"
setsid nohup python3 mock-apis/mock_server.py $ARGS >/tmp/mock.log 2>&1 & echo $! > .mock.pid; sleep 0.8; echo "mock on :$PORT (flake=${FLAKE:-1})"
