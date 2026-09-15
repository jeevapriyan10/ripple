.PHONY: up down test lint fmt logs

up:
	docker-compose up --build

down:
	docker-compose down

test:
	cd backend && python -m poetry run pytest

lint:
	cd backend && python -m poetry run ruff check . && python -m poetry run black --check . && python -m poetry run mypy ripple

fmt:
	cd backend && python -m poetry run black . && python -m poetry run ruff check . --fix

logs:
	docker-compose logs -f
