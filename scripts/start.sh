#!/usr/bin/env bash
# start.sh — standalone production entrypoint (non-Docker)
# Pushes schema then starts both services in the same process group.
# For containerised deployments use docker-compose.yml instead.
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL must be set}"

echo "→ Syncing database schema..."
pnpm --filter @workspace/db push --force
echo "✓ Schema up to date"

echo "→ Starting API server..."
pnpm --filter @workspace/api-server start &
API_PID=$!

echo "→ Starting bot..."
cd bot/polymarket-bot
python scheduler.py &
BOT_PID=$!

# Bring everything down if either process exits
wait -n $API_PID $BOT_PID
EXIT_CODE=$?
kill $API_PID $BOT_PID 2>/dev/null || true
exit $EXIT_CODE
