.PHONY: run install test docker-build docker-up docker-down docker-logs docker-staging clean

install:
	python -m venv .venv
	. .venv/bin/activate && pip install -r requirements.txt

run:
	python -m mexc_bot.main

test:
	python3 tests/test_crossing_and_remove_logic.py
	python3 tests/test_v3_futures_and_movers.py
	python3 tests/test_mover_enrichment.py
	python3 tests/test_learning_events.py
	python3 tests/test_assistant_ux.py
	python3 tests/test_v1_complete.py
	python3 tests/test_isolated_agent.py
	python3 tests/test_webapi.py

verify:
	bash scripts/verify_build.sh

stress:
	bash scripts/stress_staging.sh

desk:
	python3 -m mexc_bot.webapi

desk-https:
	bash scripts/desk_https_up.sh

test-web:
	python3 tests/test_webapi.py

# Staging: prefer droplet (see docs/DROPLET_OPS.md). Local scripts still work if needed.
staging-up:
	bash scripts/staging_up.sh

staging-down:
	bash scripts/staging_down.sh

staging-logs:
	@if [ -f .staging.log ]; then tail -f .staging.log; \
	elif command -v docker >/dev/null 2>&1; then docker logs -f mexc-alert-bot-staging; \
	else echo "Prefer droplet: ./scripts/droplet.sh staging-logs"; fi

# Requires ~/.ssh/config Host mexc-droplet
droplet-status:
	bash scripts/droplet.sh status

droplet-staging:
	bash scripts/droplet.sh deploy-staging

docker-build:
	docker compose build

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f mexc-bot

# Staging bot + data-staging volume (needs .env.staging)
docker-staging:
	docker compose --profile staging up -d --build mexc-bot-staging

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
