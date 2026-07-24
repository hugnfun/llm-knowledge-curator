#!/usr/bin/env python3
"""Stage C RAG validation: test if embedding top-3 matching beats keyword routing.

Uses Ollama qwen3-embedding:0.6b to embed 6 project profiles + 10 golden items,
then compares cosine-similarity top-3 vs keyword overlap matching.
"""

import json
import math
import urllib.request
from pathlib import Path

SAMPLES_PATH = Path(__file__).parent / "samples.json"
GOLDEN_PATH = Path(__file__).parent / "golden.json"
RESULTS_PATH = Path(__file__).parent / "rag_eval_results.json"

OLLAMA_URL = "http://127.0.0.1:11434/api/embeddings"
EMBED_MODEL = "qwen3-embedding:0.6b"

# 6 project profiles based on user focus areas
PROJECT_PROFILES = [
    {
        "id": "ai_agent",
        "name": "AI/Agent/自动化",
        "text": "AI Agent MCP 自动化工作流 知识库 Claude Code Codex Cursor Agent架构 工具调用 LLM应用",
    },
    {
        "id": "indie_dev",
        "name": "独立开发/SaaS",
        "text": "独立开发 SaaS 一人公司 商业模式 独立开发者 产品验证 MVP 订阅制 结果导向定价",
    },
    {
        "id": "xiaohongshu",
        "name": "小红书/活人感",
        "text": "小红书 小绿书 活人感 原生感 内容营销 自媒体 爆文 公众号 写作 短视频",
    },
    {
        "id": "pet_account",
        "name": "抖音宠物账号",
        "text": "抖音宠物账号 萨摩耶 宠物内容 宠物IP 萌宠视频 宠物经济 养狗",
    },
    {
        "id": "marketing_seo",
        "name": "营销/增长/SEO",
        "text": "营销 用户心理 增长 SEO 流量 关键词 转化 投流 引流 增长黑客",
    },
    {
        "id": "systems_thinking",
        "name": "系统思维/长期主义",
        "text": "第一性原理 系统思维 长期主义 底层规律 认知框架 反直觉 方法论 飞轮模型",
    },
]

# Keyword map for keyword-based matching (same profiles, keyword sets)
PROJECT_KEYWORDS = {
    "ai_agent": ["ai", "agent", "mcp", "自动化", "工作流", "知识库", "claude", "codex", "cursor", "llm"],
    "indie_dev": ["独立开发", "saas", "一人公司", "商业模式", "mvp", "订阅", "创业"],
    "xiaohongshu": ["小红书", "小绿书", "活人感", "原生感", "自媒体", "爆文", "公众号", "写作", "短视频"],
    "pet_account": ["宠物", "萨摩耶", "养狗", "萌宠", "抖音宠物"],
    "marketing_seo": ["营销", "用户心理", "增长", "seo", "流量", "关键词", "转化", "投流", "引流"],
    "systems_thinking": ["第一性原理", "系统思维", "长期主义", "底层规律", "认知框架", "反直觉", "方法论", "飞轮"],
}


def ollama_embed(text: str) -> list[float]:
    """Embed text via Ollama API."""
    payload = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data["embedding"]


def cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def keyword_match(text: str, tags: list[str], category: str) -> list[tuple[str, float]]:
    """Keyword overlap matching. Returns sorted [(project_id, score)]."""
    combined = (text[:500] + " " + " ".join(tags) + " " + category).lower()
    scores = {}
    for pid, keywords in PROJECT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw.lower() in combined)
        scores[pid] = score
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return [(pid, float(s)) for pid, s in ranked if s > 0]


def main():
    samples = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))

    # Select 10 diverse items: pick from different golden verdicts and tags
    selected_indices = [1, 3, 6, 8, 15, 19, 21, 22, 23, 38]
    selected = [(samples[i], golden[i]["golden"]) for i in selected_indices]

    print(f"Embedding {len(PROJECT_PROFILES)} project profiles + {len(selected)} items...")

    # Embed project profiles
    proj_embeddings = {}
    for p in PROJECT_PROFILES:
        emb = ollama_embed(p["text"])
        proj_embeddings[p["id"]] = emb
        print(f"  [profile] {p['id']}: dim={len(emb)}")

    # Embed items and compute matches
    results = []
    for sample, gold in selected:
        # Use title + content snippet for embedding
        item_text = sample["title"] + " " + sample["content"][:500]
        item_emb = ollama_embed(item_text)

        # Embedding top-3
        emb_scores = []
        for pid, proj_emb in proj_embeddings.items():
            sim = cosine_sim(item_emb, proj_emb)
            emb_scores.append((pid, sim))
        emb_scores.sort(key=lambda x: -x[1])
        emb_top3 = [(pid, round(s, 4)) for pid, s in emb_scores[:3]]

        # Keyword matching
        tags = gold.get("tags", [])
        # tags from golden might not exist, use category instead
        kw_scores = keyword_match(sample["content"], [], gold.get("category", ""))
        kw_top3 = [(pid, s) for pid, s in kw_scores[:3]]

        results.append({
            "unit_id": sample["unit_id"],
            "title": sample["title"][:50],
            "golden_verdict": gold["verdict"],
            "golden_category": gold["category"],
            "embedding_top3": emb_top3,
            "keyword_top3": kw_top3,
            "best_embedding_match": emb_top3[0][0] if emb_top3 else "none",
            "best_keyword_match": kw_top3[0][0] if kw_top3 else "none",
            "embedding_top1_score": emb_top3[0][1] if emb_top3 else 0,
        })

        print(f"  [{sample['unit_id']}] {sample['title'][:40]}")
        print(f"    emb: {emb_top3}")
        print(f"    kw:  {kw_top3}")

    # Summary comparison
    print(f"\n{'='*70}")
    print("Summary: Embedding vs Keyword matching")
    print(f"{'='*70}")

    emb_with_match = sum(1 for r in results if r["best_embedding_match"] != "none")
    kw_with_match = sum(1 for r in results if r["best_keyword_match"] != "none")
    avg_emb_score = sum(r["embedding_top1_score"] for r in results) / len(results)

    print(f"Items with a match:    emb={emb_with_match}/{len(results)}  kw={kw_with_match}/{len(results)}")
    print(f"Avg top-1 sim score:   {avg_emb_score:.4f}")
    print(f"\nPer-item comparison:")
    for r in results:
        emb_match = r["best_embedding_match"]
        kw_match = r["best_keyword_match"]
        agree = "==" if emb_match == kw_match else "!="
        print(f"  {r['unit_id']:28s} cat={r['golden_category']:8s} emb={emb_match:20s} kw={kw_match:20s} {agree}")

    output = {"results": results, "project_profiles": PROJECT_PROFILES}
    RESULTS_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nResults saved to {RESULTS_PATH}")

    # Decision heuristic
    agreement = sum(1 for r in results if r["best_embedding_match"] == r["best_keyword_match"])
    print(f"\nEmbedding vs keyword agreement: {agreement}/{len(results)}")
    if avg_emb_score > 0.5 and emb_with_match > kw_with_match:
        print("VERDICT: Embedding shows clear advantage -> recommend building vector store")
    elif avg_emb_score > 0.3:
        print("VERDICT: Embedding has moderate signal -> worth a larger test before committing")
    else:
        print("VERDICT: Embedding signal weak -> stick with keyword/rule routing")


if __name__ == "__main__":
    main()
