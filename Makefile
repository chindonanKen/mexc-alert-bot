.PHONY: run install docker-build docker-up docker-down docker-logs clean

install:
	python -m venv .venv
	. .venv/bin/activate && pip install -r requirements.txt

run:
	python -m mexc_bot.main

docker-build:
	docker compose build

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f mexc-bot

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
