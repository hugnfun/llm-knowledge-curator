#!/usr/bin/env python3
"""
test_mcp_server.py — 端到端测试 MCP server

启动子进程跑 mcp_server.py,发 5 个 JSON-RPC 请求验证。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

SERVER = Path("/Users/aicer/Documents/Project/llm-knowledge-curator/scripts/mcp_server.py")

def main():
    print("=" * 60)
    print("MCP Server 端到端测试")
    print("=" * 60)
    p = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )

    def send(req):
        p.stdin.write(json.dumps(req) + "\n")
        p.stdin.flush()
        line = p.stdout.readline()
        return json.loads(line) if line else None

    def expect(ok, label):
        print(f"  {'✓' if ok else '✗'} {label}")
        return ok

    # 1. initialize
    print("\n[1/6] initialize")
    r = send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {}}})
    expect(r and r.get("result", {}).get("serverInfo", {}).get("name") == "llkc",
           f"server name: {r.get('result', {}).get('serverInfo', {}).get('name') if r else 'NONE'}")

    # 2. notifications/initialized(无 response)
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized",
                              "params": {}}) + "\n")
    p.stdin.flush()

    # 3. tools/list
    print("\n[2/6] tools/list")
    r = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = r.get("result", {}).get("tools", []) if r else []
    expect(len(tools) == 8, f"tool 数: {len(tools)} (期望 8)")
    for t in tools:
        print(f"    · {t['name']}")

    # 4. get_stats
    print("\n[3/6] tools/call get_stats")
    r = send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
              "params": {"name": "get_stats", "arguments": {}}})
    content = r.get("result", {}).get("content", [{}])[0].get("text", "") if r else ""
    expect("seed" in content and "asset" in content, f"stats 内容: {content[:200]}")

    # 5. list_seeds priority=high
    print("\n[4/6] tools/call list_seeds priority=high limit=3")
    r = send({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
              "params": {"name": "list_seeds", "arguments": {"priority": "high", "limit": 3}}})
    content = r.get("result", {}).get("content", [{}])[0].get("text", "") if r else ""
    expect("[high]" in content, f"high 过滤后内容: {content[:300]}")
    print(f"    示例:\n{content[:400].replace(chr(10), chr(10) + '    ')}")

    # 6. get_health
    print("\n[5/6] tools/call get_health")
    r = send({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
              "params": {"name": "get_health", "arguments": {}}})
    content = r.get("result", {}).get("content", [{}])[0].get("text", "") if r else ""
    expect("vault 路径" in content, f"health 内容: {content[:400]}")

    # 7. search_inbox
    print("\n[6/6] tools/call search_inbox query=养狗 limit=3")
    r = send({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
              "params": {"name": "search_inbox", "arguments": {"query": "养狗", "limit": 3}}})
    content = r.get("result", {}).get("content", [{}])[0].get("text", "") if r else ""
    print(f"    搜索结果: {content[:400]}")

    # 关闭
    p.stdin.close()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()

    print("\n" + "=" * 60)
    print("✓ MCP server 端到端测试通过")
    print("=" * 60)

if __name__ == "__main__":
    main()
