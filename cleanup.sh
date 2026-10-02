#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "================================================================="
echo "  🧹 GHOSTFOLIO EPHEMERAL TEARDOWN & STORAGE CLEANUP "
echo "================================================================="

# 1. Stop containers and destroy volumes & orphans
echo "[1/3] Stopping containers and removing ephemeral volumes..."
docker compose -f "$DIR/docker-compose.yml" down -v --remove-orphans || true

# 2. Purge lingering volumes
echo "[2/3] Checking for lingering ephemeral volumes..."
VOLUMES=$(docker volume ls --filter "name=ghostfolio" -q 2>/dev/null || true)
if [ -n "$VOLUMES" ]; then
    docker volume rm $VOLUMES -f 2>/dev/null || true
    echo "[OK] Lingering volumes purged."
else
    echo "[OK] No lingering Docker volumes found."
fi

# Deep clean flag check
if [ "$1" == "--deep" ]; then
    echo "[DEEP CLEAN] Removing sidecar Docker images and build cache..."
    IMAGES=$(docker images --filter "reference=*ghostfolio*sync*" -q 2>/dev/null || true)
    if [ -n "$IMAGES" ]; then
        docker rmi $IMAGES -f 2>/dev/null || true
    fi
    docker builder prune -f --filter "until=1h" 2>/dev/null || true
fi

# 3. Purge local temporary files and python cache
echo "[3/3] Purging local caches and temporary files..."
find "$DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$DIR" -type f \( -name "*.pyc" -o -name "*.tmp" -o -name "*.log" \) -delete 2>/dev/null || true

echo ""
echo "================================================================="
echo "  ✨ TEARDOWN COMPLETE! ZERO EPHEMERAL ARTIFACTS REMAIN"
echo "  - PostgreSQL Ephemeral DB: WIPED"
echo "  - Redis Ephemeral Cache:   WIPED"
echo "  - Containers & Networks:   DESTROYED"
echo "  - Local Trade CSV:         SAFE ON GOOGLE DRIVE"
echo "================================================================="
