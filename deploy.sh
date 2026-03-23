#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

mkdir -p data

echo "[deploy] building and starting..."
docker compose up -d --build

echo "[deploy] done. open: http://<host-ip>:8088"
