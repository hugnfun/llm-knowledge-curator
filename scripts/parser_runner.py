#!/usr/bin/env python3
"""
parser_runner.py — 调 ark-code-latest 批量判别 953 条 inbox 单元。

输出:
  output/verdicts.jsonl       一行一条 verdict JSON (含 unit_id + 判别结果 + raw_response)
  output/parser_errors.jsonl  失败/重试日志
  output/progress.json        进度状态(可断点续跑)
"""

import os
import re
import json
import time
import sys
import threading
import concurrent.futures as cf
from pathlib import Path
from urllib import request, error

PROJ = Path(os.path.expanduser("~/Documents/Project/llm-knowledge-curator"))
OUT = PROJ / "output"
OUT.mkdir(parents=True, exist_ok=True)
PROMPT_PATH = PROJ / "prompts" / "parser_v0.2.md"

API_BASE = "https://ark.cn-beijing.volces.com/api/coding/v3"
API_KEY = "5321a60a-2cdd-440f-a730-37787d642c13"
MODEL = "ark-code-latest"
CONCURRENCY = 5
TIMEOUT = 90
MAX_INPUT_CHARS = 12000  # 单条内容截断阈值(留 prompt + system 余量)
MAX_RETRY = 2

VAULT = Path(os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/LLM知识库"
))

VERDICT_FILE = OUT / "verdicts.jsonl"
ERROR_FILE = OUT / "parser_errors.jsonl"
PROGRESS_FILE = OUT / "progress.json"
INDEX_FILE = OUT / "inbox_index.json"

# ---------- 读 prompt ----------
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")

# ---------- 内容读取 ----------
TG_MSG_HEADER = re.compile(r"^## (\d{2}:\d{2})\s*$", re.MULTILINE)


def fetch_unit_content(unit: dict) -> str:
    """根据 unit 元数据从文件系统取完整内容。"""
    path = Path(unit["abs_path"])
    if not path.exists():
        raise FileNotFoundError(unit["abs_path"])
    text = path.read_text(encoding="utf-8", errors="ignore")
    if unit["source"] != "telegram":
        return text
    # telegram 需要切到具体 message
    idx = unit["tg_message_idx"]
    headers = [(m.group(1), m.start(), m.end()) for m in TG_MSG_HEADER.finditer(text)]
    if not headers:
        return text
    # 取第 idx 条(1-based,与 build_index 一致)
    target = idx - 1
    # 注意 build_index 做过相邻去重,这里按时间戳找该序号
    # 简化:直接按 idx 切对应那段,允许小幅 drift
    if target >= len(headers):
        target = len(headers) - 1
    _, _, end = headers[target]
    next_start = headers[target + 1][1] if target + 1 < len(headers) else len(text)
    body = text[end:next_start].strip()
    body = re.sub(r"^---+\s*$", "", body, flags=re.MULTILINE).strip()
    return body


# ---------- LLM 调用 ----------
_lock_write = threading.Lock()


def call_llm(content: str, unit_meta: dict, attempt: int = 0) -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"# 待判别单元\n\n"
                    f"- source: {unit_meta['source']}\n"
                    f"- title: {unit_meta['title']}\n"
                    f"- source_path: {unit_meta['source_path']}\n"
                    f"- char_len: {unit_meta['char_len']}\n\n"
                    f"## 内容\n\n{content}\n\n"
                    f"---\n\n"
                    f"按 v0.2 规范判别,**只返回一个 JSON 对象**,不要附加解释/markdown 代码块。"
                ),
            },
        ],
        "temperature": 0.2,
        "max_tokens": 800,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        f"{API_BASE}/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        parsed = json.loads(raw)
        text = parsed["choices"][0]["message"]["content"]
        # 抠 JSON
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise ValueError(f"no json in response: {text[:200]}")
        verdict = json.loads(m.group(0))
        return {"ok": True, "verdict": verdict, "raw": text}
    except (error.URLError, TimeoutError, ConnectionError) as e:
        if attempt < MAX_RETRY:
            time.sleep(2 + attempt * 3)
            return call_llm(content, unit_meta, attempt + 1)
        return {"ok": False, "error": f"net: {e}"}
    except Exception as e:
        if attempt < MAX_RETRY and "json" not in str(e).lower():
            time.sleep(1 + attempt * 2)
            return call_llm(content, unit_meta, attempt + 1)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------- 持久化 ----------
def load_done_ids() -> set:
    """从 verdicts.jsonl 读已完成的 unit_id,支持断点续跑。"""
    if not VERDICT_FILE.exists():
        return set()
    done = set()
    for line in VERDICT_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            done.add(obj["unit_id"])
        except Exception:
            pass
    return done


def write_verdict(record: dict):
    with _lock_write:
        with VERDICT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_error(record: dict):
    with _lock_write:
        with ERROR_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------- 单元处理 ----------
def process_unit(unit: dict) -> dict:
    try:
        content = fetch_unit_content(unit)
    except Exception as e:
        rec = {"unit_id": unit["unit_id"], "ok": False, "error": f"read: {e}"}
        write_error(rec)
        return rec

    truncated = False
    if len(content) > MAX_INPUT_CHARS:
        content = content[:MAX_INPUT_CHARS]
        truncated = True

    result = call_llm(content, unit)
    if result["ok"]:
        record = {
            "unit_id": unit["unit_id"],
            "source": unit["source"],
            "source_path": unit["source_path"],
            "abs_path": unit["abs_path"],
            "title": unit["title"],
            "tg_message_idx": unit["tg_message_idx"],
            "tg_message_time": unit["tg_message_time"],
            "char_len": unit["char_len"],
            "truncated": truncated,
            "verdict": result["verdict"],
        }
        write_verdict(record)
        return {"unit_id": unit["unit_id"], "ok": True, "verdict": result["verdict"].get("verdict")}
    else:
        write_error({"unit_id": unit["unit_id"], **result, "source_path": unit["source_path"]})
        return {"unit_id": unit["unit_id"], "ok": False, "error": result.get("error")}


# ---------- 主流程 ----------
def main():
    units = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    done = load_done_ids()
    pending = [u for u in units if u["unit_id"] not in done]

    total = len(units)
    print(f"[INIT] total={total} done_already={len(done)} pending={len(pending)} concurrency={CONCURRENCY}")
    if not pending:
        print("[DONE] 全部完成,无需处理。")
        return

    t0 = time.time()
    counters = {"ok": 0, "fail": 0, "seed": 0, "asset": 0, "archive": 0, "other": 0}

    with cf.ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futures = {ex.submit(process_unit, u): u for u in pending}
        for i, fut in enumerate(cf.as_completed(futures), start=1):
            res = fut.result()
            if res["ok"]:
                counters["ok"] += 1
                v = res.get("verdict") or "other"
                counters[v] = counters.get(v, 0) + 1
            else:
                counters["fail"] += 1
            if i % 20 == 0 or i == len(pending):
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed else 0
                eta = (len(pending) - i) / rate if rate else 0
                print(
                    f"[PROGRESS] {i}/{len(pending)} "
                    f"ok={counters['ok']} fail={counters['fail']} "
                    f"seed={counters.get('seed',0)} asset={counters.get('asset',0)} "
                    f"archive={counters.get('archive',0)} "
                    f"rate={rate:.2f}/s eta={eta:.0f}s"
                )

    PROGRESS_FILE.write_text(json.dumps({
        "finished_at": time.time(),
        "elapsed_sec": time.time() - t0,
        "counters": counters,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[FINAL] elapsed={time.time()-t0:.1f}s counters={counters}")


if __name__ == "__main__":
    main()
