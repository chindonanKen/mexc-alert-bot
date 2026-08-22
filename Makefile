.PHONY: run install test docker-build docker-up docker-down docker-logs docker-staging clean desk-qa db-safety pre-deploy smoke smoke-staging

install:
	python -m venv .venv
	. .venv/bin/activate && pip install -r requirements.txt

run:
	python -m mexc_bot.main

test:
	@PY=$$(test -x .venv/bin/python3 && echo .venv/bin/python3 || echo python3); \
	$$PY tests/test_crossing_and_remove_logic.py && \
	$$PY tests/test_v3_futures_and_movers.py && \
	$$PY tests/test_mover_enrichment.py && \
	$$PY tests/test_learning_events.py && \
	$$PY tests/test_desk_learning_spine.py && \
	$$PY tests/test_trade_learning.py && \
	$$PY tests/test_super_agent.py && \
	$$PY tests/test_assistant_ux.py && \
	$$PY tests/test_v1_complete.py && \
	$$PY tests/test_isolated_agent.py && \
	$$PY tests/test_webapi.py && \
	$$PY tests/test_audio_convert.py && \
	$$PY tests/test_mover_dedupe.py && \
	$$PY tests/test_mover_wick_fire.py && \
	$$PY tests/test_db_safety.py && \
	$$PY tests/test_desk_bot_shared_alerts.py && \
	$$PY tests/test_mw_data_safety.py && \
	$$PY tests/test_daily_target_report.py && \
	$$PY tests/test_red_streak.py && \
	$$PY tests/test_p1_retrieve.py && \
	$$PY tests/test_p1_cases.py && \
	$$PY tests/test_visual_ad.py && \
	$$PY tests/test_desk_slice1_search.py && \
	$$PY tests/test_heartbeat.py

verify:
	bash scripts/verify_build.sh

# Hard rule: never wipe SQLite on deploy/rebuild/desk update (see docs/DB_SAFETY.md)
db-safety:
	python3 scripts/db_safety_check.py

pre-deploy:
	bash scripts/pre_deploy_db_guard.sh

# After a container rebuild: polling + heartbeat + row counts (see docs/PROD_RELIABILITY.md)
smoke:
	bash scripts/post_deploy_smoke.sh --container mexc-alert-bot --db data/alerts.db

smoke-staging:
	bash scripts/post_deploy_smoke.sh --container mexc-alert-bot-staging --db data-staging/alerts.db --skip-watchlist-floor

# Mandatory after AD Desk edits: multi-agent panel + mark pass (see AGENTS.md)
desk-qa:
	@echo "=== AD Desk QA (mandatory after webapi/learning/static changes) ==="
	@echo "1) Run in Grok: /workflow desk-qa   OR  workflow tool name=desk-qa"
	@echo "   args: {\"focus\": \"<one-line description of this change>\"}"
	@echo "2) Fix blockers if FAIL"
	@echo "3) Mark pass:  python3 scripts/desk_qa_gate.py pass --note 'desk-qa PASS: …'"
	@echo ""
	@python3 scripts/desk_qa_gate.py status

stress:
	bash scripts/stress_staging.sh

desk:
	python3 -m mexc_bot.webapi

# Fast local loop: venv + .env + reload + open Xplor (http://127.0.0.1:8080)
desk-dev:
	bash scripts/desk_dev.sh

# Dummy alarms / movers / memory for local desk (never touches droplet)
desk-seed:
	.venv/bin/python scripts/seed_desk_local.py --force

desk-https:
	bash scripts/desk_https_up.sh

desk-docker:
	bash scripts/desk_up.sh

test-web:
	python3 tests/test_webapi.py
	python3 tests/test_audio_convert.py

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
