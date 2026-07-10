.PHONY: run install test docker-build docker-up docker-down docker-logs docker-staging clean

install:
	python -m venv .venv
	. .venv/bin/activate && pip install -r requirements.txt

run:
	python -m mexc_bot.main

test:
	python tests/test_crossing_and_remove_logic.py
	python tests/test_v3_futures_and_movers.py

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
