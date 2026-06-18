.PHONY: demo start stop clean build logs logs-all logs-agents logs-sim \
        test test-unit test-integration test-e2e test-all seed status help check-env

# ─── Demo ─────────────────────────────────────────────────────────────────────

demo: check-env
	@echo "Starting SRE Copilot demo..."
	@echo "This takes ~3 minutes to fully initialize."
	@echo ""
	docker compose up --build -d
	@echo ""
	@echo "All services starting. Use 'make status' to check."
	@echo ""
	@echo "Access points (ready in ~3 minutes):"
	@echo "  SRE Dashboard : http://localhost:8000"
	@echo "  Grafana       : http://localhost:3000  (admin/admin)"
	@echo "  Kafka UI      : http://localhost:8080"
	@echo "  Mailhog       : http://localhost:8025"
	@echo "  Prometheus    : http://localhost:9090"
	@echo "  Qdrant        : http://localhost:6333/dashboard"
	@echo ""
	@echo "Watch emails appear at http://localhost:8025 during incidents."
	@echo "First failure injected ~2 minutes after startup."

start: check-env
	docker compose up -d

stop:
	docker compose down

clean:
	docker compose down -v --remove-orphans
	docker system prune -f

# ─── Build ────────────────────────────────────────────────────────────────────

build:
	docker compose build

seed:
	docker compose run --rm knowledge-seeder

# ─── Logs ─────────────────────────────────────────────────────────────────────

logs:
	docker compose logs -f detection-agent correlation-agent investigation-agent \
	    knowledge-retrieval-agent remediation-agent communication-agent postmortem-agent

logs-all:
	docker compose logs -f

logs-sim:
	docker compose logs -f traffic-simulator failure-injector deployment-simulator

logs-ingest:
	docker compose logs -f metrics-ingester log-ingester deployment-ingester

logs-api:
	docker compose logs -f sre-api

# ─── Status ───────────────────────────────────────────────────────────────────

status:
	docker compose ps

# ─── Testing ──────────────────────────────────────────────────────────────────

## Install test dependencies
test-install:
	pip install -r tests/requirements.txt -q

## Run unit + integration tests (primary CI command — no docker compose required)
test: test-install
	pytest tests/unit tests/integration \
	    -v --tb=short \
	    --cov=agents --cov=shared --cov=ingestion --cov=simulation --cov=knowledge \
	    --cov-report=term-missing \
	    --ignore=tests/e2e

## Fast unit tests only — no Docker at all, completes in <30 seconds
test-unit: test-install
	pytest tests/unit -v --tb=short -m unit

## Integration tests — requires Docker (testcontainers auto-manages containers)
test-integration: test-install
	pytest tests/integration -v --tb=short -m integration

## E2E tests — requires 'make demo' running first
test-e2e: test-install
	E2E=true pytest tests/e2e -v --tb=short -m e2e -s

## Contract tests — schema and API contract validation
test-contracts: test-install
	pytest tests/contracts -v --tb=short -m contract

## Run everything including e2e (requires live stack)
test-all: test-install
	E2E=true pytest tests/ \
	    -v --tb=short \
	    --cov=agents --cov=shared --cov=ingestion --cov=simulation --cov=knowledge \
	    --cov-report=term-missing \
	    --cov-report=html:htmlcov

# ─── Helpers ──────────────────────────────────────────────────────────────────

check-env:
	@if [ -z "$(ANTHROPIC_API_KEY)" ]; then \
	    if [ ! -f .env ]; then \
	        echo "No .env file found. Set ANTHROPIC_API_KEY:"; \
	        echo "  cp .env.example .env && edit .env"; \
	        exit 1; \
	    fi; \
	fi

help:
	@echo "SRE Copilot — Makefile targets"
	@echo ""
	@echo "  Demo:"
	@echo "    make demo          Build and start everything"
	@echo "    make start         Start without rebuilding"
	@echo "    make stop          Stop all services"
	@echo "    make clean         Stop and remove all volumes"
	@echo "    make status        Show service health"
	@echo ""
	@echo "  Logs:"
	@echo "    make logs          Follow agent logs"
	@echo "    make logs-sim      Follow simulator logs"
	@echo "    make logs-ingest   Follow ingestion logs"
	@echo "    make logs-all      Follow all logs"
	@echo ""
	@echo "  Testing:"
	@echo "    make test          Unit + integration tests (no docker compose needed)"
	@echo "    make test-unit     Fast unit tests only (<30 seconds)"
	@echo "    make test-integration  Integration tests (Docker auto-managed)"
	@echo "    make test-e2e      Full E2E (requires: make demo)"
	@echo "    make test-all      Everything including E2E"
