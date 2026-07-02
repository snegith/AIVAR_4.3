.PHONY: up down test logs

up:
	docker compose up -d --build

down:
	docker compose down

test:
	pytest

logs:
	docker compose logs -f api postgres
