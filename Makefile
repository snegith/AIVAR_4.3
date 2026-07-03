.PHONY: up down test logs migrate simulate langfuse-up

up:
	docker compose up -d --build

down:
	docker compose down

migrate:
	alembic upgrade head

test:
	pytest

simulate:
	python simulate.py --dry-run --seed 42

langfuse-up:
	docker compose --profile langfuse up -d --build

logs:
	docker compose logs -f api postgres
