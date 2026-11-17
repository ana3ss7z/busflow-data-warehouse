-include .env
export

PY ?= .venv/bin/python

.PHONY: up down seed simulate load test rebuild perf

up:
	docker compose up -d
	./scripts/wait-for-db.sh

down:
	docker compose down

seed:
	$(PY) src/generate_reference.py

test:
	$(PY) -m pytest -q

simulate load rebuild perf:
	@echo "$@: not implemented yet"