.PHONY: up down test logs migrate

up:
	docker compose up -d --build

down:
	docker compose down

migrate:
	alembic upgrade head

test:
	pytest

logs:
	docker compose logs -f api postgres
