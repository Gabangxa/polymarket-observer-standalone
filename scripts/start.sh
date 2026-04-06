#!/usr/bin/env bash
# start.sh — production entrypoint for Replit VM
# Runs on every deploy: pushes schema, then starts both services.
set -euo pipefail

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

# If either process exits, bring down the other and exit non-zero
wait -n $API_PID $BOT_PID
EXIT_CODE=$?
kill $API_PID $BOT_PID 2>/dev/null || true
exit $EXIT_CODE
