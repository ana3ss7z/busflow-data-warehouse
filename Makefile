.PHONY: up down seed simulate load test rebuild perf

up:
	docker compose up -d
	./scripts/wait-for-db.sh

down:
	docker compose down

seed simulate load test rebuild perf:
	@echo "$@: not implemented yet"
