#!/usr/bin/env bash
set -Eeuo pipefail

: "${POSTGRES_APP_PASSWORD:?POSTGRES_APP_PASSWORD is required}"
: "${POSTGRES_WORKER_PASSWORD:?POSTGRES_WORKER_PASSWORD is required}"

psql \
  --username "${POSTGRES_USER}" \
  --dbname "${POSTGRES_DB}" \
  --set=ON_ERROR_STOP=1 \
  --set=app_password="${POSTGRES_APP_PASSWORD}" \
  --set=worker_password="${POSTGRES_WORKER_PASSWORD}" <<'SQL'
CREATE ROLE blogops_app
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS
    PASSWORD :'app_password';
CREATE ROLE blogops_worker
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS
    PASSWORD :'worker_password';
GRANT CONNECT ON DATABASE blogops TO blogops_app, blogops_worker;
SQL
