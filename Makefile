.PHONY: up down test logs migrate simulate langfuse-up lint typecheck deploy smoke

up:
	docker compose up -d --build

down:
	docker compose down

migrate:
	alembic upgrade head

lint:
	ruff check app sim simulate.py tests

typecheck:
	mypy app

test:
	pytest

simulate:
	python simulate.py --dry-run --seed 42

langfuse-up:
	docker compose --profile langfuse up -d --build

# --- AWS free-tier deploy (run on the EC2 box after ec2_bootstrap.sh) ---
deploy:
	bash deploy/deploy.sh

# Post-deploy validation gate: simulate.py on RDS + Persona C < WATCH_THRESHOLD.
smoke:
	bash deploy/post_deploy_smoke.sh

logs:
	docker compose logs -f api postgres
