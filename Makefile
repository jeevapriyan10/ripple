# Makefile for Ripple Fraud Detection Platform
.PHONY: help up down test lint fmt logs

DOCKER_COMPOSE ?= $(shell if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo "docker compose"; else echo "docker-compose"; fi)

help:
	@echo "Available commands:"
	@echo "  make up     - Build and start all services via Docker Compose"
	@echo "  make down   - Stop and tear down running containers"
	@echo "  make logs   - Tail logs from all running containers"
	@echo "  make test   - Run backend pytest test suite"
	@echo "  make lint   - Run ruff, black --check, and mypy"
	@echo "  make fmt    - Format code with black and ruff --fix"

up:
	$(DOCKER_COMPOSE) up --build

down:
	$(DOCKER_COMPOSE) down

logs:
	$(DOCKER_COMPOSE) logs -f

test:
	@if command -v poetry >/dev/null 2>&1; then \
		cd backend && poetry run pytest tests; \
	elif $(DOCKER_COMPOSE) ps --services --filter "status=running" 2>/dev/null | grep -q backend; then \
		$(DOCKER_COMPOSE) exec backend pytest tests; \
	elif command -v docker >/dev/null 2>&1; then \
		docker build --target tester -t ripple-backend-tester ./backend >/dev/null && docker run --rm ripple-backend-tester; \
	else \
		cd backend && pytest tests; \
	fi

lint:
	@if command -v poetry >/dev/null 2>&1; then \
		cd backend && poetry run ruff check . && poetry run black --check . && poetry run mypy ripple; \
	elif $(DOCKER_COMPOSE) ps --services --filter "status=running" 2>/dev/null | grep -q backend; then \
		$(DOCKER_COMPOSE) exec backend ruff check . && $(DOCKER_COMPOSE) exec backend black --check . && $(DOCKER_COMPOSE) exec backend mypy ripple; \
	elif command -v docker >/dev/null 2>&1; then \
		docker build --target tester -t ripple-backend-tester ./backend >/dev/null && \
		docker run --rm ripple-backend-tester poetry run ruff check . && \
		docker run --rm ripple-backend-tester poetry run black --check . && \
		docker run --rm ripple-backend-tester poetry run mypy ripple; \
	else \
		cd backend && ruff check . && black --check . && mypy ripple; \
	fi

fmt:
	@if command -v poetry >/dev/null 2>&1; then \
		cd backend && poetry run black . && poetry run ruff check --fix .; \
	elif $(DOCKER_COMPOSE) ps --services --filter "status=running" 2>/dev/null | grep -q backend; then \
		$(DOCKER_COMPOSE) exec backend black . && $(DOCKER_COMPOSE) exec backend ruff check --fix .; \
	elif command -v docker >/dev/null 2>&1; then \
		docker build --target tester -t ripple-backend-tester ./backend >/dev/null && \
		docker run --rm -v "$$(pwd)/backend/ripple:/app/ripple" -v "$$(pwd)/backend/tests:/app/tests" ripple-backend-tester poetry run black . && \
		docker run --rm -v "$$(pwd)/backend/ripple:/app/ripple" -v "$$(pwd)/backend/tests:/app/tests" ripple-backend-tester poetry run ruff check --fix .; \
	else \
		cd backend && black . && ruff check --fix .; \
	fi
