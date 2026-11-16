#!/usr/bin/env bash
set -euo pipefail
# Waits until the db container reports healthy.
for i in $(seq 1 30); do
  status=$(docker inspect -f '{{.State.Health.Status}}' busflow-db 2>/dev/null || echo "starting")
  if [ "$status" = "healthy" ]; then echo "db is healthy"; exit 0; fi
  sleep 2
done
echo "db did not become healthy in time" >&2
exit 1
