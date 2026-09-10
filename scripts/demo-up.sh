#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO="$ROOT/demo/signalops"
INFRA="$DEMO/infra"
PID_DIR="$DEMO/.run"
mkdir -p "$PID_DIR"

if [ ! -f "$INFRA/.env" ]; then
  cp "$INFRA/.env.example" "$INFRA/.env"
  echo "Created $INFRA/.env from the demo template."
fi

if [ "$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)" -lt 262144 ]; then
  echo "OpenSearch requires vm.max_map_count >= 262144."
  echo "Run: sudo sysctl -w vm.max_map_count=262144"
  exit 1
fi

cd "$INFRA"
docker compose up -d

echo "Waiting for demo infrastructure..."
docker compose up -d --wait prometheus alertmanager opensearch otel-collector kafka redis postgres grafana

for service in opensearch-init kafka-init; do
  cid=$(docker compose ps -aq "$service")
  code=$(docker inspect -f '{{.State.ExitCode}}' "$cid")
  if [ "$code" != "0" ]; then
    docker compose logs "$service" >&2
    exit 1
  fi
done

cd "$ROOT"

if [ -f "$PID_DIR/simulator.pid" ] && kill -0 "$(cat "$PID_DIR/simulator.pid")" 2>/dev/null; then
  echo "Simulator already running."
else
  nohup uv run python "$DEMO/simulator/simulator.py" >"$PID_DIR/simulator.log" 2>&1 &
  echo $! >"$PID_DIR/simulator.pid"
fi

if [ -f "$PID_DIR/receiver.pid" ] && kill -0 "$(cat "$PID_DIR/receiver.pid")" 2>/dev/null; then
  echo "Alert receiver already running."
else
  nohup uv run uvicorn sre_copilot.alerting.receiver:app --host 0.0.0.0 --port 8080 >"$PID_DIR/receiver.log" 2>&1 &
  echo $! >"$PID_DIR/receiver.pid"
fi

sleep 2
curl -fsS http://localhost:8000/ >/dev/null
curl -fsS http://localhost:8080/healthz >/dev/null

echo
cat <<'EOF'
SRE Copilot demo is up.

  Simulator       http://localhost:8000
  Break incident  curl -X POST localhost:8000/break
  Heal incident   curl -X POST localhost:8000/heal
  Prometheus      http://localhost:9090
  Alertmanager    http://localhost:9093
  Grafana         http://localhost:3000
  OpenSearch      http://localhost:9200
  Kafka           localhost:29092
  Redis           localhost:6379
  Receiver        http://localhost:8080/healthz

Logs:
  demo/signalops/.run/simulator.log
  demo/signalops/.run/receiver.log
EOF
