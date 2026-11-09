#!/bin/sh
# Creates a read-only role for Grafana (least privilege: the dashboard cannot modify data).
# Runs once, when the database volume is first initialised.
set -eu

psql -v ON_ERROR_STOP=1 \
     -v gpw="$GRAFANA_DB_PASSWORD" \
     -v dbname="$POSTGRES_DB" \
     -v owner="$POSTGRES_USER" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE grafana_ro LOGIN PASSWORD :'gpw';
GRANT CONNECT ON DATABASE :"dbname" TO grafana_ro;
GRANT USAGE ON SCHEMA public TO grafana_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public GRANT SELECT ON TABLES TO grafana_ro;
SQL
