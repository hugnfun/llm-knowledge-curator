#!/usr/bin/env python3
"""Run parser v0.3 LLM against golden set samples and compute metrics."""

import json
import sys
import time
import concurrent.futures as cf
from pathlib import Path
from collections import Counter, defaultdict

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llkc import config
from llkc.llm_client import call_llm, extract_json

SAMPLES_PATH = Path(__file__).parent / "samples.json"
GOLDEN_PATH = Path(__file__).parent / "golden.json"
RESULTS_PATH = Path(__file__).parent / "eval_results.json"

PROMPT = config.PARSER_PROMPT_PATH.read_text(encoding="utf-8")


import re
 
SAMPLES_PATH = Path(__file__).parent / "samples.json"
GOLDEN_PATH = Path(__file__).parent / "golden.json"
RESULTS_PATH = Path(__file__).parent / "eval_results.json"
 
PROMPT = config.PARSER_PROMPT_PATH.read_text(encoding="utf-8")
 
 
def extract_json_safe(text: str) -> dict:
     """Robust JSON extraction: strip markdown, handle truncation."""
     if not text or not text.strip():
         raise ValueError("empty response")
     # Strip markdown code fences
     text = re.sub(r"^```(?:json)?\s*", "", text.strip())
     text = re.sub(r"\s*```$", "", text.strip())
     # Try direct parse first
     try:
         return json.loads(text)
     except json.JSONDecodeError:
         pass
     # Try regex extract
     m = re.search(r"\{[\s\S]*\}", text)
     if m:
         try:
             return json.loads(m.group(0))
         except json.JSONDecodeError:
             pass
     # Try repairing truncated JSON by adding closing braces
     m = re.search(r"\{[\s\S]*", text)
     if m:
         fragment = m.group(0)
         open_braces = fragment.count("{") - fragment.count("}")
         if open_braces > 0:
             repaired = fragment + "}" * open_braces
             try:
                 return json.loads(repaired)
             except json.JSONDecodeError:
                 pass
     raise ValueError(f"cannot parse JSON from: {text[:200]}")


def classify(sample: dict) -> dict:
    """Send one sample to the LLM and return parsed verdict."""
    content = sample["content"]
    if len(content) > config.PARSER_MAX_INPUT_CHARS:
        content = content[:config.PARSER_MAX_INPUT_CHARS]
    messages = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": (
            f"# 待判别单元\n\n"
            f"- source: {sample['source']}\n"
            f"- title: {sample['title']}\n"
            f"- source_path: {sample.get('source_path', '')}\n"
            f"- char_len: {sample['char_len']}\n\n"
            f"## 内容\n\n{content}\n\n---\n\n"
            f"按 v0.3 决策树规范判别,**只返回一个 JSON 对象**"
        )},
    ]
    result = call_llm(
        messages, temperature=0.2, max_tokens=1200,
        timeout=config.PARSER_TIMEOUT, max_retry=config.PARSER_MAX_RETRY,
    )
    if not result["ok"]:
        return {"unit_id": sample["unit_id"], "ok": False, "error": result.get("error")}
    try:
        verdict = extract_json_safe(result["text"])
    except Exception as e:
        return {"unit_id": sample["unit_id"], "ok": False, "error": f"json: {e}", "raw": result["text"][:200]}
    return {"unit_id": sample["unit_id"], "ok": True, "verdict": verdict}


def compute_metrics(golden: list, results: list) -> dict:
    """Compute accuracy, confusion matrix, per-class precision/recall/F1."""
    gold_map = {g["unit_id"]: g["golden"] for g in golden}
    res_map = {r["unit_id"]: r for r in results}

    labels = ["seed", "asset", "archive"]
    n = 0
    correct = 0
    confusion = {a: {b: 0 for b in labels} for a in labels}
    mismatches = []

    for uid, gold in gold_map.items():
        res = res_map.get(uid)
        if not res or not res.get("ok"):
            continue
        n += 1
        gv = gold["verdict"]
        rv = res["verdict"].get("verdict", "archive")
        if rv not in labels:
            rv = "archive"
        confusion[gv][rv] += 1
        if gv == rv:
            correct += 1
        else:
            mismatches.append({
                "unit_id": uid,
                "golden_verdict": gv,
                "llm_verdict": rv,
                "golden_category": gold["category"],
                "llm_category": res["verdict"].get("category", ""),
                "golden_priority": gold["priority"],
                "llm_priority": res["verdict"].get("priority", ""),
                "reason": res["verdict"].get("reason", "")[:60],
            })

    accuracy = correct / n if n else 0

    # Per-class metrics
    per_class = {}
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[o][label] for o in labels) - tp
        fn = sum(confusion[label][o] for o in labels) - tp
        precision = tp / (tp + fp) if (tp + fp) else 0
        recall = tp / (tp + fn) if (tp + fn) else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0
        per_class[label] = {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
                             "tp": tp, "fp": fp, "fn": fn}

    # Category accuracy (for non-archive items where both have categories)
    cat_match = 0
    cat_total = 0
    for uid, gold in gold_map.items():
        res = res_map.get(uid)
        if not res or not res.get("ok"):
            continue
        if gold["verdict"] == res["verdict"].get("verdict", "") and gold["category"]:
            cat_total += 1
            if gold["category"] == res["verdict"].get("category", ""):
                cat_match += 1

    # Priority accuracy
    pri_match = 0
    pri_total = 0
    for uid, gold in gold_map.items():
        res = res_map.get(uid)
        if not res or not res.get("ok"):
            continue
        pri_total += 1
        if gold["priority"] == res["verdict"].get("priority", ""):
            pri_match += 1

    return {
        "n": n,
        "accuracy": round(accuracy, 3),
        "confusion_matrix": confusion,
        "per_class": per_class,
        "category_accuracy": round(cat_match / cat_total, 3) if cat_total else 0,
        "priority_accuracy": round(pri_match / pri_total, 3) if pri_total else 0,
        "mismatches": mismatches,
    }


def main():
    samples = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(samples)} samples, {len(golden)} golden annotations")
    print(f"Model: {config.LLM_MODEL}, API: {config.LLM_API_BASE}")

    results = []
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(classify, s): s for s in samples}
        for i, fut in enumerate(cf.as_completed(futures), 1):
            res = fut.result()
            results.append(res)
            status = "ok" if res.get("ok") else "FAIL"
            v = res.get("verdict", {}).get("verdict", "?") if res.get("ok") else res.get("error", "")[:30]
            print(f"  [{i:2d}/{len(samples)}] {res['unit_id']:28s} {status:4s} {v}")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")

    metrics = compute_metrics(golden, results)
    RESULTS_PATH.write_text(
        json.dumps({"results": results, "metrics": metrics}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"\n{'='*60}")
    print(f"Accuracy: {metrics['accuracy']:.1%} ({metrics['n']} items)")
    print(f"Category accuracy: {metrics['category_accuracy']:.1%}")
    print(f"Priority accuracy: {metrics['priority_accuracy']:.1%}")
    print(f"\nConfusion matrix (rows=golden, cols=LLM):")
    print(f"{'':>10s}  {'seed':>6s}  {'asset':>6s}  {'archive':>8s}")
    for label in ["seed", "asset", "archive"]:
        row = metrics["confusion_matrix"][label]
        print(f"{label:>10s}  {row['seed']:>6d}  {row['asset']:>6d}  {row['archive']:>8d}")
    print(f"\nPer-class F1:")
    for label in ["seed", "asset", "archive"]:
        c = metrics["per_class"][label]
        print(f"  {label:>8s}: P={c['precision']:.2f} R={c['recall']:.2f} F1={c['f1']:.2f}")
    print(f"\nMismatches ({len(metrics['mismatches'])}):")
    for m in metrics["mismatches"]:
        print(f"  {m['unit_id']:28s} gold={m['golden_verdict']:7s} llm={m['llm_verdict']:7s} cat:{m['golden_category']:8s}->{m['llm_category']:8s}")
    print(f"\nResults saved to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
