#!/usr/bin/env python3
"""
writer_agent.py — 从 Daily Thinking 文档生成 4 角度小绿书候选

用法:
  python3 scripts/writer_agent.py                  # 今天
  python3 scripts/writer_agent.py --date 2026-06-30
  python3 scripts/writer_agent.py --force          # 覆盖已生成的草稿
  python3 scripts/writer_agent.py --allow-empty    # 即便"今日自由写"为空也跑(默认拒)

策略:
  - 读 vault/02-思考/<date>.md
  - 提取"今日自由写"段落 + 5 条素材 frontmatter
  - 自由写空 → 拒跑(防 token 浪费)
  - 调 ark-code-latest 一次性吐 4 个 JSON 候选
  - 落到 vault/02-思考/<date>-drafts/draft-A.md (B/C/D)
"""

import os
import re
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import date
from urllib import request, error

PROJ = Path(os.path.expanduser("~/Documents/Project/llm-knowledge-curator"))
PROMPT_PATH = PROJ / "prompts" / "writer_v0.1.md"

VAULT = Path(os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/LLM知识库"
))
THINKING_ROOT = VAULT / "02-思考"

API_BASE = os.environ.get("WRITER_API_BASE", "https://ark.cn-beijing.volces.com/api/coding/v3")
API_KEY = os.environ.get("WRITER_API_KEY", "5321a60a-2cdd-440f-a730-37787d642c13")
MODEL = os.environ.get("WRITER_MODEL", "ark-code-latest")
TIMEOUT = 180
MAX_RETRY = 2

FREE_WRITE_RE = re.compile(
    r"##\s*今日自由写\s*\n(.+?)\n##\s*今日.*?灵感素材",
    re.DOTALL,
)
FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
PLACEHOLDER_RE = re.compile(r"^\s*<!--.*?-->\s*$", re.MULTILINE)
# 模板里的 italic 占位提示行(整行被 `_..._` 包裹的就是模板生成的提示)
ITALIC_HINT_RE = re.compile(r"^\s*_[^_\n]+_\s*$", re.MULTILINE)


def repair_inner_quotes(json_str: str) -> str:
    """兜底修复:LLM 偶尔在 JSON 字符串字段里塞裸的 " (英文双引号)。
    策略:逐字符扫描,在字符串字段内部出现的 " 后面如果不接 , } ] : 空白结尾,
    就把它替换成中文 “”。简化实现:外层用状态机判'是否在 string'。"""
    out = []
    in_str = False
    i = 0
    n = len(json_str)
    while i < n:
        c = json_str[i]
        if c == "\\" and i + 1 < n:
            # 转义对原样保留
            out.append(c)
            out.append(json_str[i + 1])
            i += 2
            continue
        if c == '"':
            if not in_str:
                in_str = True
                out.append(c)
                i += 1
                continue
            # 在字符串内遇到 " — 看后面是不是真正结束符
            # 真结束: " 后是 , : } ] 换行 空格 (可能多次)然后 , : } ]
            j = i + 1
            while j < n and json_str[j] in " \t\r\n":
                j += 1
            if j >= n or json_str[j] in ',:}]':
                in_str = False
                out.append(c)
                i += 1
                continue
            # 否则: 内部裸引号,换成中文「
            out.append("「" if (i == 0 or json_str[i - 1] not in "「『") else "」")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def extract_free_write(text: str) -> str:
    """提取'今日自由写'内容,去掉:HTML 注释占位、italic 提示行、分割线。
    完全清掉模板生成痕迹后剩多少字 = 用户真实写的字数。"""
    m = FREE_WRITE_RE.search(text)
    if not m:
        return ""
    body = m.group(1)
    body = PLACEHOLDER_RE.sub("", body)
    body = ITALIC_HINT_RE.sub("", body)
    body = re.sub(r"^-{3,}\s*$", "", body, flags=re.MULTILINE)
    return body.strip()


def extract_seeds_section(text: str) -> str:
    """提取整段灵感素材的原文(给 prompt 用)。"""
    idx = text.find("## 今日")
    # 找到第二个 ## 今日 (灵感素材) 开始
    occurrences = [m.start() for m in re.finditer(r"##\s*今日", text)]
    if len(occurrences) < 2:
        return ""
    return text[occurrences[1]:].strip()


def call_llm(daily_doc_text: str, free_write: str, seeds_section: str,
             target_date: str, attempt: int = 0) -> dict:
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    user_msg = (
        f"# 今日 Daily Thinking ({target_date})\n\n"
        f"## 用户今日自由写\n\n{free_write}\n\n"
        f"## 今日 5 条灵感素材(供你参考,可选用)\n\n{seeds_section}\n\n"
        f"---\n\n"
        f"按 writer_v0.1 规范产出 4 个角度的 JSON 数组,不要附加解释。"
    )
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.7,
        "max_tokens": 10000,
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
        # 抠 JSON 数组
        m = re.search(r"\[[\s\S]*\]", text)
        if not m:
            raise ValueError(f"no json array in response: {text[:300]}")
        json_str = m.group(0)
        try:
            drafts = json.loads(json_str)
        except json.JSONDecodeError as je1:
            # 尝试兜底修复内部裸引号
            try:
                repaired = repair_inner_quotes(json_str)
                drafts = json.loads(repaired)
                print(f"  ⚠ JSON 兜底修复成功(内部裸引号已替换为「」)",
                      file=sys.stderr)
            except json.JSONDecodeError as je2:
                debug = Path("/tmp/writer_raw_last.txt")
                debug.write_text(text, encoding="utf-8")
                debug2 = Path("/tmp/writer_repaired_last.txt")
                debug2.write_text(repaired, encoding="utf-8")
                return {"ok": False,
                        "error": f"JSON 解析失败(初:{je1}; 修复后:{je2})。"
                                 f"raw→{debug}, repaired→{debug2}",
                        "raw_text": text}
        return {"ok": True, "drafts": drafts, "raw": text,
                "usage": parsed.get("usage", {})}
    except (error.URLError, TimeoutError, ConnectionError) as e:
        if attempt < MAX_RETRY:
            time.sleep(3 + attempt * 5)
            return call_llm(daily_doc_text, free_write, seeds_section,
                            target_date, attempt + 1)
        return {"ok": False, "error": f"net: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def render_draft_md(draft: dict, target_date: str, source_doc: str) -> str:
    """把单个 draft JSON 渲染成可在 obsidian 里直接读的 md 文件"""
    angle_id = draft.get("angle_id", "?")
    angle_name = draft.get("angle_name", "未命名角度")
    headline = draft.get("headline", "")
    body = draft.get("draft", "")
    hook = draft.get("hook", "")
    image_count = draft.get("image_count", 0)
    linked = draft.get("linked_seeds", [])
    laf = draft.get("laf_self_score", {})
    weak = draft.get("weak_points", "")

    # frontmatter
    linked_str = ", ".join(repr(x) for x in linked)
    laf_lines = []
    for k, v in laf.items():
        # 单行 yaml 兼容:把内嵌的换行/冒号塞回单 line
        v_safe = str(v).replace("\n", " ").replace('"', "'")
        laf_lines.append(f'  {k}: "{v_safe}"')
    laf_block = "\n".join(laf_lines) if laf_lines else ""

    fm = (
        "---\n"
        f"type: draft\n"
        f"date: {target_date}\n"
        f"source_doc: {source_doc}\n"
        f"angle_id: {angle_id}\n"
        f"angle_name: {angle_name}\n"
        f"linked_seeds: [{linked_str}]\n"
        f"image_count: {image_count}\n"
        f"status: candidate\n"
    )
    if laf_block:
        fm += "laf_self_score:\n" + laf_block + "\n"
    fm += "---\n"

    md = (
        fm
        + f"\n# 角度 {angle_id} · {angle_name}\n\n"
        + f"## 首行炸点\n\n> {headline}\n\n"
        + f"## 正文\n\n{body}\n\n"
        + f"## 结尾钩子\n\n> {hook}\n\n"
        + f"## 模型自评弱点\n\n{weak}\n\n"
        + f"## 关联 seed\n\n"
    )
    for uid in linked:
        md += f"- [[{uid}]]\n"
    return md


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--force", action="store_true",
                    help="覆盖已存在的 draft 目录")
    ap.add_argument("--allow-empty", action="store_true",
                    help="允许'今日自由写'为空时也跑")
    ap.add_argument("--dry-run", action="store_true",
                    help="只解析+预览输入,不调 LLM")
    ap.add_argument("--model", default=None,
                    help="覆盖模型 (默认: 环境变量 WRITER_MODEL 或 ark-code-latest)")
    ap.add_argument("--api-key", default=None,
                    help="覆盖 API Key (默认: 环境变量 WRITER_API_KEY)")
    ap.add_argument("--api-base", default=None,
                    help="覆盖 API Base URL (默认: 环境变量 WRITER_API_BASE)")
    ap.add_argument("--suffix", default=None,
                    help="draft 目录后缀,切模型对比时自动分目录(如 --suffix deepseek)")
    args = ap.parse_args()
    # 命令行覆盖 > 环境变量 > 硬编码默认值
    model = args.model or MODEL
    api_key = args.api_key or API_KEY
    api_base = args.api_base or API_BASE
    # 自动 suffix: 如果指定了 --model 但没指定 --suffix,用模型名简写
    suffix = args.suffix
    if suffix is None and args.model:
        # deepseek-v4-pro → deepseek, ark-code-latest → ark
        suffix = args.model.split("-")[0]
    # 重新绑定到模块级变量,让 call_llm 读到
    import writer_agent
    writer_agent.MODEL = model
    writer_agent.API_KEY = api_key
    writer_agent.API_BASE = api_base

    daily_doc = THINKING_ROOT / f"{args.date}.md"
    if not daily_doc.exists():
        print(f"[FATAL] {daily_doc} 不存在,先跑 daily_thinking.py", file=sys.stderr)
        sys.exit(1)

    text = daily_doc.read_text(encoding="utf-8")
    free_write = extract_free_write(text)
    seeds_section = extract_seeds_section(text)

    print(f"📄 源文档: {daily_doc.name}")
    print(f"   自由写: {len(free_write)} 字")
    print(f"   素材段: {len(seeds_section)} 字")

    if not free_write and not args.allow_empty:
        print(
            "[REFUSE] '今日自由写'为空。先写下你今天的想法再跑 writer。\n"
            "         如确认要让 AI 完全从素材生成,加 --allow-empty(不推荐,易出 AI 味)",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.dry_run:
        print("\n--- 自由写预览 ---")
        print(free_write[:500] or "(空)")
        print("\n--- 素材段预览(前 600 字) ---")
        print(seeds_section[:600])
        return

    dir_name = f"{args.date}-drafts"
    if suffix:
        dir_name += f"-{suffix}"
    drafts_dir = THINKING_ROOT / dir_name
    if drafts_dir.exists() and not args.force:
        existing = list(drafts_dir.glob("*.md"))
        if existing:
            print(
                f"[REFUSE] {drafts_dir.name} 已有 {len(existing)} 个草稿。"
                f"要重写加 --force。",
                file=sys.stderr,
            )
            sys.exit(2)

    print(f"\n⏳ 调用 {MODEL} 生成 4 角度候选 ...")
    t0 = time.time()
    result = call_llm(text, free_write, seeds_section, args.date)
    elapsed = time.time() - t0

    if not result["ok"]:
        print(f"[FATAL] LLM 调用失败: {result['error']}", file=sys.stderr)
        sys.exit(3)

    drafts = result["drafts"]
    if not isinstance(drafts, list):
        print(f"[FATAL] LLM 返回不是数组: {type(drafts)}", file=sys.stderr)
        sys.exit(3)

    print(f"✓ 收到 {len(drafts)} 个候选 (耗时 {elapsed:.1f}s, "
          f"tokens={result['usage'].get('total_tokens', '?')})")

    drafts_dir.mkdir(parents=True, exist_ok=True)
    # 备份原始 raw 响应,debug 用
    (drafts_dir / "_raw_response.json").write_text(
        json.dumps(drafts, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for d in drafts:
        aid = d.get("angle_id", "X")
        md = render_draft_md(d, args.date, daily_doc.name)
        target = drafts_dir / f"draft-{aid}.md"
        target.write_text(md, encoding="utf-8")
        headline = d.get("headline", "")[:40]
        word_count = len(d.get("draft", ""))
        print(f"  ✓ {target.name:<14s} "
              f"[{d.get('angle_name', '?'):<15s}] {word_count}字 · {headline}")

    print(f"\n📂 全部草稿落在: {drafts_dir}")


if __name__ == "__main__":
    main()
