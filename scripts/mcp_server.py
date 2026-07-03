#!/usr/bin/env python3
"""
mcp_server.py — LLM 知识库 MCP Server

Model Context Protocol over stdio. 让任何 MCP client(Claude Code / Hermes Agent /
未来 Obsidian 插件的 MCP client)用统一协议调用:

  - daily_thinking       :  生成/检查今日 Daily Thinking 文档
  - write_drafts          :  调 writer_agent 生成 4 角度候选
  - run_parser            :  跑 parser 增量
  - list_seeds            :  列 seed(支持 priority/category/source 过滤)
  - get_stats             :  vault 状态(seed/asset/archive/draft 计数)
  - get_health            :  路径/API key/最近 cron 状态自检
  - search_inbox          :  全文搜 inbox(给 LLM 反查用)
  - read_seed             :  读单条 seed 详情

设计原则:
  - 零外部依赖(只用 stdlib),适配任何 venv
  - 所有方法同步执行 + timeout 保护
  - 错误返回标准 MCP error code(-32603 internal error)
  - 写操作(daily/write/parser)前打印 dry-run 摘要,真正执行后才返回结果

JSON-RPC 2.0 over stdio(line-delimited):
  request:  {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"...","arguments":{...}}}
  response: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"..."}], "isError": false}}
"""
import json
import os
import sys
import time
import traceback
import subprocess
from pathlib import Path
from datetime import date, datetime

# === 配置 ===
PROJ = Path(os.environ.get("LLKC_PROJ", "/Users/aicer/Documents/Project/llm-knowledge-curator"))
VAULT = Path(os.environ.get("LLKC_VAULT", "/Users/aicer/Library/Mobile Documents/iCloud~md~obsidian/Documents/LLM知识库"))
PYTHON = os.environ.get("LLKC_PYTHON", sys.executable)

# === JSON-RPC 2.0 transport ===
def read_message():
    """读一行 JSON"""
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"_parse_error": line}

def write_message(msg):
    """写一行 JSON"""
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()

def ok(id_, result):
    return {"jsonrpc": "2.0", "id": id_, "result": result}

def err(id_, code, message, data=None):
    payload = {"code": code, "message": message}
    if data is not None:
        payload["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": payload}

# === 工具实现 ===
def tool_daily_thinking(args: dict) -> dict:
    """生成/检查 Daily Thinking 文档"""
    target_date = args.get("date") or date.today().isoformat()
    seeds_count = int(args.get("seeds", 5))
    seed_param = str(seeds_count)
    force = args.get("force", False)
    cmd = [PYTHON, str(PROJ / "scripts" / "daily_thinking.py"),
           "--date", target_date, "--seeds", seed_param]
    if force:
        cmd.append("--force")
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        return {"isError": True,
                "content": [{"type": "text",
                             "text": f"daily_thinking 失败(exit={out.returncode}):\n{out.stderr[-500:]}"}]}
    # 抓文件路径
    target_file = VAULT / "02-思考" / f"{target_date}.md"
    return {"isError": False,
            "content": [{"type": "text",
                         "text": f"✓ Daily Thinking 生成成功\n日期: {target_date}\n路径: {target_file}\n{out.stdout.strip()[:500]}"}]}

def tool_write_drafts(args: dict) -> dict:
    """调 writer_agent 生成 4 角度候选"""
    target_date = args.get("date") or date.today().isoformat()
    model = args.get("model", os.environ.get("WRITER_MODEL", "ark-code-latest"))
    api_base = args.get("api_base", os.environ.get("WRITER_API_BASE",
                                                     "https://ark.cn-beijing.volces.com/api/coding/v3"))
    api_key = args.get("api_key", os.environ.get("WRITER_API_KEY",
                                                  "5321a60a-2cdd-440f-a730-37787d642c13"))
    cmd = [PYTHON, str(PROJ / "scripts" / "writer_agent.py"),
           "--date", target_date,
           "--model", model,
           "--api-base", api_base,
           "--api-key", api_key]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        return {"isError": True,
                "content": [{"type": "text",
                             "text": f"writer_agent 失败(exit={out.returncode}):\n{out.stderr[-500:]}"}]}
    drafts_dir = VAULT / "02-思考" / f"{target_date}-drafts-{model.split('-')[0]}"
    return {"isError": False,
            "content": [{"type": "text",
                         "text": f"✓ Writer 完成\n模型: {model}\n目录: {drafts_dir}\n\n{out.stdout.strip()[-1000:]}"}]}

def tool_run_parser(args: dict) -> dict:
    """跑 parser 增量(同 cron_incremental.sh)"""
    cmd = ["bash", str(PROJ / "scripts" / "cron_incremental.sh")]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if out.returncode != 0:
        return {"isError": True,
                "content": [{"type": "text",
                             "text": f"parser 失败(exit={out.returncode}):\n{out.stderr[-500:]}"}]}
    return {"isError": False,
            "content": [{"type": "text",
                         "text": f"✓ Parser 增量完成\n\n{out.stdout.strip()[-1500:]}"}]}

def tool_list_seeds(args: dict) -> dict:
    """列 seed(支持 priority/category/source 过滤)"""
    priority = args.get("priority")  # high/normal
    category = args.get("category")
    source = args.get("source")      # telegram/clippings/x-bookmarks
    limit = int(args.get("limit", 50))
    seed_root = VAULT / "01-灵感库"
    if not seed_root.exists():
        return {"isError": True,
                "content": [{"type": "text", "text": f"seed 目录不存在: {seed_root}"}]}
    seeds = []
    for f in seed_root.rglob("*.md"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        # 抠 frontmatter
        if not text.startswith("---"):
            continue
        try:
            fm_end = text.index("\n---\n", 3)
            fm_text = text[3:fm_end]
        except ValueError:
            continue
        fm = {}
        for line in fm_text.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip().strip('"').strip("'")
        if priority and fm.get("priority") != priority:
            continue
        if category and fm.get("category") != category:
            continue
        if source and fm.get("source") != source:
            continue
        seeds.append({
            "unit_id": fm.get("unit_id", f.stem),
            "source": fm.get("source", "?"),
            "category": fm.get("category", "?"),
            "priority": fm.get("priority", "normal"),
            "trigger": fm.get("trigger", "")[:80],
            "tags": fm.get("tags", "[]"),
            "path": str(f.relative_to(VAULT)),
        })
    seeds.sort(key=lambda s: (s["priority"] != "high", s["category"], s["unit_id"]))
    total = len(seeds)
    seeds = seeds[:limit]
    lines = [f"共 {total} 条 seed (limit={limit}):"]
    for s in seeds:
        lines.append(f"  [{s['priority']:<6s}] [{s['source']:<11s}] [{s['category']:<10s}] {s['unit_id']}  {s['trigger']}")
    return {"isError": False,
            "content": [{"type": "text", "text": "\n".join(lines)}]}

def tool_get_stats(args: dict) -> dict:
    """vault 状态统计"""
    def count_md(p: Path) -> int:
        return len(list(p.rglob("*.md"))) if p.exists() else 0
    seed = count_md(VAULT / "01-灵感库")
    asset = count_md(VAULT / "03-Assets")
    archive = count_md(VAULT / "04-Archive")
    inbox = {
        "Clippings": count_md(VAULT / "00-Inbox" / "Clippings"),
        "Telegram": count_md(VAULT / "00-Inbox" / "Telegram"),
        "X-Bookmarks": count_md(VAULT / "00-Inbox" / "X-Bookmarks"),
    }
    thinking_root = VAULT / "02-思考"
    daily_count = count_md(thinking_root)
    drafts_count = sum(1 for p in thinking_root.iterdir()
                       if p.is_dir() and p.name.endswith("-drafts") or "-drafts-" in p.name)
    return {"isError": False,
            "content": [{"type": "text",
                         "text": (f"📊 LLM 知识库统计\n"
                                  f"  vault: {VAULT}\n"
                                  f"  seed (01-灵感库): {seed}\n"
                                  f"  asset (03-Assets): {asset}\n"
                                  f"  archive (04-Archive): {archive}\n"
                                  f"  inbox 拆分: Clippings={inbox['Clippings']} "
                                  f"Telegram={inbox['Telegram']} X-Bookmarks={inbox['X-Bookmarks']}\n"
                                  f"  Daily Thinking 文档: {daily_count}\n"
                                  f"  draft 目录: {drafts_count}")}]}

def tool_get_health(args: dict) -> dict:
    """自检: 路径/脚本/API key/最近 cron 状态"""
    checks = []
    # 1. vault 路径
    ok = VAULT.exists() and (VAULT / "00-Inbox").exists()
    checks.append(f"{'✓' if ok else '✗'} vault 路径: {VAULT}")
    # 2. PROJ 脚本
    for s in ["daily_thinking.py", "writer_agent.py", "parser_runner.py",
              "build_index.py", "write_back.py", "cron_incremental.sh"]:
        ok = (PROJ / "scripts" / s).exists()
        checks.append(f"{'✓' if ok else '✗'} script: {s}")
    # 3. API key
    ark = os.environ.get("WRITER_API_KEY", "")
    ds = os.environ.get("DEEPSEEK_API_KEY", "")
    checks.append(f"{'✓' if ark else '⚠'} WRITER_API_KEY (ark): {'set' if ark else 'unset'}")
    checks.append(f"{'✓' if ds else '⚠'} DEEPSEEK_API_KEY: {'set' if ds else 'unset'}")
    # 4. 最近 cron 日志
    log_dir = PROJ / "output" / "cron_logs"
    if log_dir.exists():
        logs = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if logs:
            last = logs[0]
            age_min = (time.time() - last.stat().st_mtime) / 60
            checks.append(f"  最近 cron 日志: {last.name} ({age_min:.0f} 分钟前)")
        else:
            checks.append("  ⚠ 无 cron 日志")
    else:
        checks.append("  ⚠ cron_logs 目录不存在")
    return {"isError": False,
            "content": [{"type": "text", "text": "🩺 LLM 知识库健康检查:\n" + "\n".join(checks)}]}

def tool_search_inbox(args: dict) -> dict:
    """全文搜 inbox(给 LLM 反查用)"""
    query = args.get("query", "").strip()
    limit = int(args.get("limit", 20))
    if not query:
        return {"isError": True,
                "content": [{"type": "text", "text": "query 不能为空"}]}
    inbox = VAULT / "00-Inbox"
    matches = []
    for f in inbox.rglob("*.md"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            if query in text:
                # 找第一个含 query 的行
                snippet = ""
                for line in text.splitlines():
                    if query in line:
                        snippet = line[:200]
                        break
                matches.append({
                    "path": str(f.relative_to(VAULT)),
                    "snippet": snippet,
                })
                if len(matches) >= limit:
                    break
        except: pass
    if not matches:
        return {"isError": False,
                "content": [{"type": "text", "text": f"未找到含 '{query}' 的 inbox 文件"}]}
    lines = [f"搜索 '{query}' 找到 {len(matches)} 条:"]
    for m in matches:
        lines.append(f"  · {m['path']}\n    {m['snippet']}")
    return {"isError": False,
            "content": [{"type": "text", "text": "\n".join(lines)}]}

def tool_read_seed(args: dict) -> dict:
    """读单条 seed 详情"""
    unit_id = args.get("unit_id", "").strip()
    if not unit_id:
        return {"isError": True,
                "content": [{"type": "text", "text": "unit_id 不能为空"}]}
    seed_root = VAULT / "01-灵感库"
    found = None
    for f in seed_root.rglob(f"{unit_id}*.md"):
        found = f
        break
    if not found:
        return {"isError": True,
                "content": [{"type": "text", "text": f"未找到 seed: {unit_id}"}]}
    text = found.read_text(encoding="utf-8", errors="ignore")
    return {"isError": False,
            "content": [{"type": "text", "text": text[:3000]}]}

# === Tool 注册表 ===
TOOLS = {
    "daily_thinking": {
        "description": "生成或检查指定日期的 Daily Thinking 文档。返回文件路径。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "日期 YYYY-MM-DD(默认今天)"},
                "seeds": {"type": "integer", "description": "随机 seed 数量(默认 5)"},
                "force": {"type": "boolean", "description": "覆盖已存在文档"},
            },
        },
        "fn": tool_daily_thinking,
    },
    "write_drafts": {
        "description": "调 writer_agent 生成 4 角度小绿书候选。需要先有 Daily Thinking 文档。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "日期 YYYY-MM-DD"},
                "model": {"type": "string", "description": "模型(默认 ark-code-latest)"},
                "api_base": {"type": "string", "description": "API base URL(可选)"},
                "api_key": {"type": "string", "description": "API key(可选,默认从 env)"},
            },
        },
        "fn": tool_write_drafts,
    },
    "run_parser": {
        "description": "跑 parser 增量(扫 inbox → 判别 → 落盘)。耗时长,可能要 1-2 分钟。",
        "inputSchema": {"type": "object", "properties": {}},
        "fn": tool_run_parser,
    },
    "list_seeds": {
        "description": "列 seed,支持按 priority/category/source 过滤。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "priority": {"type": "string", "description": "high/normal"},
                "category": {"type": "string", "description": "共鸣补充/概念/方法论/案例/..."},
                "source": {"type": "string", "description": "telegram/clippings/x-bookmarks"},
                "limit": {"type": "integer", "description": "最多返回数(默认 50)"},
            },
        },
        "fn": tool_list_seeds,
    },
    "get_stats": {
        "description": "vault 状态统计(seed/asset/archive/draft 计数)。",
        "inputSchema": {"type": "object", "properties": {}},
        "fn": tool_get_stats,
    },
    "get_health": {
        "description": "健康自检: 路径/脚本/API key/最近 cron 状态。",
        "inputSchema": {"type": "object", "properties": {}},
        "fn": tool_get_health,
    },
    "search_inbox": {
        "description": "在 00-Inbox 全文搜,给 LLM 反查用。返回文件路径+上下文行。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索词"},
                "limit": {"type": "integer", "description": "最多返回(默认 20)"},
            },
            "required": ["query"],
        },
        "fn": tool_search_inbox,
    },
    "read_seed": {
        "description": "读单条 seed 完整内容(前 3000 字)。",
        "inputSchema": {
            "type": "object",
            "properties": {"unit_id": {"type": "string", "description": "如 telegram-0206"}},
            "required": ["unit_id"],
        },
        "fn": tool_read_seed,
    },
}

# === MCP protocol handlers ===
def handle_initialize(id_, params):
    return ok(id_, {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {
            "name": "llkc",
            "version": "0.1.0",
        },
    })

def handle_tools_list(id_, params):
    return ok(id_, {
        "tools": [
            {
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["inputSchema"],
            }
            for name, spec in TOOLS.items()
        ]
    })

def handle_tools_call(id_, params):
    name = params.get("name")
    arguments = params.get("arguments", {})
    if name not in TOOLS:
        return err(id_, -32602, f"unknown tool: {name}")
    spec = TOOLS[name]
    try:
        result = spec["fn"](arguments)
        return ok(id_, result)
    except Exception as e:
        return ok(id_, {
            "isError": True,
            "content": [{"type": "text",
                         "text": f"tool '{name}' 内部错误:\n{traceback.format_exc()[:1000]}"}],
        })

# === Main loop ===
def main():
    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        id_ = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params", {})
        if method == "initialize":
            write_message(handle_initialize(id_, params))
        elif method == "notifications/initialized":
            # 客户端发的通知,无 response
            continue
        elif method == "tools/list":
            write_message(handle_tools_list(id_, params))
        elif method == "tools/call":
            write_message(handle_tools_call(id_, params))
        elif method == "ping":
            write_message(ok(id_, {}))
        else:
            if id_ is not None:
                write_message(err(id_, -32601, f"method not found: {method}"))

if __name__ == "__main__":
    main()
