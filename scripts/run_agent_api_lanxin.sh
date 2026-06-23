#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-8787}"
export ZETTAI_STATIC_ROOT="${ZETTAI_STATIC_ROOT:-$ROOT_DIR}"
export ZETTAI_PUBLIC_URL="${ZETTAI_PUBLIC_URL:-http://172.16.80.19:$PORT}"
export ZETTAI_FRONTEND_URL="${ZETTAI_FRONTEND_URL:-$ZETTAI_PUBLIC_URL/agent-api/}"
export ZETTAI_DB="${ZETTAI_DB:-/mnt/probe_nvme0n1p4/hetgpu_tmp/zettaimvp/zettai_agent_api.sqlite3}"
export ZETTAI_DEV_LOGIN="${ZETTAI_DEV_LOGIN:-1}"

mkdir -p "$(dirname "$ZETTAI_DB")"
exec python3 "$ROOT_DIR/api/zettai_agent_api.py" --host "$HOST" --port "$PORT"
