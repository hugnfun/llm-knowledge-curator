#!/usr/bin/env bash
# LLM 知识库增量同步 cron 脚本
# 每天跑一次:扫 inbox → 判别新增 → 落盘 → 汇报
#
# 设计原则:
# 1. 静默成功(无新内容时不输出),只在有新增/有错误时输出
# 2. 任何步骤失败立即 exit 1,让 cron 通知
# 3. 全部 stdout 既给 cron 又落本地日志,便于回溯
# 4. 必须先校验 vault 路径存在 (memory 教训)

set -euo pipefail

PROJ="$HOME/Documents/Project/llm-knowledge-curator"
VAULT="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/LLM知识库"
LOG_DIR="$PROJ/output/cron_logs"
STAMP=$(date +%Y-%m-%d_%H%M%S)
LOG="$LOG_DIR/$STAMP.log"

mkdir -p "$LOG_DIR"

# === 硬校验:vault 路径必须存在 (memory) ===
if [ ! -d "$VAULT" ]; then
  echo "[FATAL] Vault 路径不存在: $VAULT" >&2
  exit 1
fi

if [ ! -d "$VAULT/00-Inbox" ]; then
  echo "[FATAL] Inbox 不存在: $VAULT/00-Inbox" >&2
  exit 1
fi

# === 环境 ===
cd "$PROJ"

# 注: ARK_API_KEY 直接 hardcode 在 parser_runner.py 中,cron 无需注入

{
  echo "=== $STAMP cron 增量同步开始 ==="
  
  # 1) 重建 index (扫描整个 inbox)
  PRE_TOTAL=0
  if [ -f output/inbox_index.json ]; then
    PRE_TOTAL=$(python3 -c "import json;print(len(json.load(open('output/inbox_index.json'))))")
  fi
  echo "[1/4] build_index..."
  python3 scripts/build_index.py >/dev/null
  NEW_TOTAL=$(python3 -c "import json;print(len(json.load(open('output/inbox_index.json'))))")
  DELTA=$((NEW_TOTAL - PRE_TOTAL))
  echo "  inbox: $PRE_TOTAL → $NEW_TOTAL (Δ$DELTA)"
  
  # 2) 算出 pending (verdicts.jsonl 没有的 unit_id)
  PENDING=$(python3 - <<'PY'
import json
units = json.load(open("output/inbox_index.json"))
done = set()
try:
    for ln in open("output/verdicts.jsonl"):
        if ln.strip():
            done.add(json.loads(ln)["unit_id"])
except FileNotFoundError:
    pass
try:
    for ln in open("output/parser_errors.jsonl"):
        if ln.strip():
            done.add(json.loads(ln)["unit_id"])
except FileNotFoundError:
    pass
print(sum(1 for u in units if u["unit_id"] not in done))
PY
)
  echo "  pending: $PENDING"
  
  # 早退:无新增 = 静默成功
  if [ "$PENDING" -eq 0 ]; then
    echo "[OK] 无新增,静默退出"
    exit 0
  fi
  
  # 3) 跑 parser 增量
  echo "[2/4] parser_runner ($PENDING 条)..."
  python3 scripts/parser_runner.py 2>&1 | tail -5
  
  # 4) 跑 write_back 增量落盘 (write_back.py 内部基于 verdicts.jsonl 全量但写盘幂等)
  echo "[3/4] write_back..."
  python3 scripts/write_back.py 2>&1 | tail -15
  
  echo "[4/4] DONE"
  echo "=== $STAMP cron 完成 ==="
} 2>&1 | tee "$LOG"
