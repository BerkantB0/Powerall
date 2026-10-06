#!/bin/sh
set -eu
# Variables are passed through psql quoting, never concatenated into SQL.
export POWERALL_APP_PASSWORD="$(cat /run/secrets/database_password)"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<'SQL'
\getenv app_password POWERALL_APP_PASSWORD
SELECT format('CREATE ROLE powerall LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %L', :'app_password') \gexec
CREATE DATABASE powerall OWNER powerall;
SQL
unset POWERALL_APP_PASSWORD
