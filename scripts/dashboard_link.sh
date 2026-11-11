#!/bin/sh
# Prints Grafana links that show the latest 14 SIMULATED days (the dashboard clock is simulated time, so the fixed default range
# falls behind as the simulation runs). Usage: sh scripts/dashboard_link.sh 

M=$(docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB" -Atc "select (extract(epoch from max(sim_ts))*1000)::bigint from greenhouse_reading"' | tr -d '\r')
[ -n "$M" ] || { echo "no readings yet"; exit 1; }
F=$((M - 14*24*3600*1000)); T=$((M + 3600*1000))
echo "http://127.0.0.1:3001/d/greengrow?from=$F&to=$T&refresh=10s"
