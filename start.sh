#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "================================================================="
echo "  🚀 GHOSTFOLIO EPHEMERAL PIPELINE: 1-COMMAND RUNNER "
echo "================================================================="

# 1. Ephemeral Secret Generation (In-Memory Only)
export POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-$(openssl rand -hex 16 2>/dev/null || od -vN "16" -An -tx1 /dev/urandom | tr -d " \n")}
export JWT_SECRET_KEY=${JWT_SECRET_KEY:-$(openssl rand -hex 32 2>/dev/null || od -vN "32" -An -tx1 /dev/urandom | tr -d " \n")}
export ACCESS_TOKEN_SALT=${ACCESS_TOKEN_SALT:-$(openssl rand -hex 16 2>/dev/null || od -vN "16" -An -tx1 /dev/urandom | tr -d " \n")}

if [ ! -f "$DIR/.env" ] && [ -f "$DIR/.env.example" ]; then
    cp "$DIR/.env.example" "$DIR/.env"
fi

echo "[1/4] Starting ephemeral services (PostgreSQL, Redis, Ghostfolio)..."
docker compose -f "$DIR/docker-compose.yml" up -d postgres redis ghostfolio

echo "[2/4] Waiting for Ghostfolio healthcheck..."
until curl -s -f http://localhost:3333/api/v1/health > /dev/null; do
    echo -n "."
    sleep 3
done
echo ""
echo "[OK] Ghostfolio is ready!"

echo "[3/4] Ingesting portfolio trades..."
docker compose -f "$DIR/docker-compose.yml" run --rm sync-job

echo "[4/4] Opening Ghostfolio dashboard..."
if which xdg-open > /dev/null; then
    xdg-open http://localhost:3333
elif which open > /dev/null; then
    open http://localhost:3333
fi

echo ""
echo "================================================================="
echo "  ✨ PIPELINE COMPLETE! GHOSTFOLIO IS READY"
echo "  Dashboard: http://localhost:3333"
echo "  To stop and wipe ephemeral containers when finished: ./cleanup.sh"
echo "================================================================="
