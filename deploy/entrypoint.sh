#!/usr/bin/env sh
set -eu

mkdir -p "${ARTIFACT_ROOT:-/app/backend/data/artifacts}" \
         "${WORKSPACE_ROOT:-/app/backend/data/workspaces}" \
         "${TERRAFORM_PLUGIN_CACHE_DIR:-/app/backend/data/plugin-cache}"

if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  echo "Applying database migrations..."
  python -m alembic upgrade head
fi

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}"
