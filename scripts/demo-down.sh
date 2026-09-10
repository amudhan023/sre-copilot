#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO="$ROOT/demo/signalops"
PID_DIR="$DEMO/.run"

for name in simulator receiver; do
  file="$PID_DIR/$name.pid"
  if [ -f "$file" ]; then
    pid=$(cat "$file")
    kill "$pid" 2>/dev/null || true
    rm -f "$file"
  fi
done

cd "$DEMO/infra"
docker compose down --remove-orphans
