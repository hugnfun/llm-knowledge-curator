#!/usr/bin/env bash
# Health check for Qwen3TTS (9999) and STT (9998) services.
# Checks /health endpoint; if down, attempts launchctl kickstart.
# Exit 0 = all healthy, 1 = one or more unhealthy.
set -uo pipefail

LOG_DIR="$HOME/Documents/Project/llm-knowledge-curator/output/cron_logs"
mkdir -p "$LOG_DIR"
STAMP=$(date +%Y-%m-%d_%H%M%S)
LOG="$LOG_DIR/health_$STAMP.log"

HEALTHY=0

check_service() {
  local name="$1" port="$2" label="$3"
  local resp
  resp=$(curl -s --noproxy '*' --max-time 5 "http://127.0.0.1:${port}/health" 2>/dev/null)
  if echo "$resp" | grep -q '"status":"ok"'; then
    echo "[$STAMP] $name (:$port) OK: $resp" | tee -a "$LOG"
  else
    echo "[$STAMP] $name (:$port) DOWN: ${resp:-no response}" | tee -a "$LOG"
    echo "[$STAMP] Attempting restart: launchctl kickstart gui/$(id -u)/$label" | tee -a "$LOG"
    launchctl kickstart "gui/$(id -u)/$label" 2>>"$LOG"
    sleep 5
    resp=$(curl -s --noproxy '*' --max-time 5 "http://127.0.0.1:${port}/health" 2>/dev/null)
    if echo "$resp" | grep -q '"status":"ok"'; then
      echo "[$STAMP] $name (:$port) RECOVERED after restart" | tee -a "$LOG"
    else
      echo "[$STAMP] $name (:$port) STILL DOWN after restart" | tee -a "$LOG"
      HEALTHY=1
    fi
  fi
}

check_service "TTS" 9999 "com.local.qwen3tts"
check_service "STT" 9998 "com.local.qwen3tts-stt"

# Clean up logs older than 7 days
find "$LOG_DIR" -name "health_*.log" -mtime +7 -delete 2>/dev/null

exit $HEALTHY
