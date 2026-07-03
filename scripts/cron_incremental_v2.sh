#!/usr/bin/env bash
# LLM Knowledge Curator v2 cron - uses llkc package CLI
# Run: scan inbox -> classify new -> pool to vault
set -euo pipefail

PROJ="$HOME/Documents/Project/llm-knowledge-curator"
VAULT="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/LLM知识库"
LOG_DIR="$PROJ/output/cron_logs"
STAMP=$(date +%Y-%m-%d_%H%M%S)
LOG="$LOG_DIR/$STAMP.log"
mkdir -p "$LOG_DIR"

if [ ! -d "$VAULT/00-Inbox" ]; then
  echo "[FATAL] Inbox not found: $VAULT/00-Inbox" >&2
  exit 1
fi

cd "$PROJ"

{
  echo "=== $STAMP llkc incremental start ==="
  python3 scripts/cli.py incremental
  echo "=== $STAMP llkc incremental done ==="
} 2>&1 | tee "$LOG"
