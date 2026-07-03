.PHONY: up down test logs migrate simulate

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

logs:
	docker compose logs -f api postgres
