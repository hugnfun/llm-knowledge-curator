"""Shared LLM API client — extracted from parser_runner and writer_agent."""

import json
import re
import time
from urllib import request, error
from typing import Optional

from . import config


def call_llm(messages: list[dict], model: Optional[str] = None,
             api_base: Optional[str] = None, api_key: Optional[str] = None,
             temperature: float = 0.2, max_tokens: int = 800,
             timeout: int = 90, max_retry: int = 2) -> dict:
    """Call an OpenAI-compatible chat completions endpoint.
    Returns {"ok": True, "text": ..., "usage": {...}} or {"ok": False, "error": ...}.
    """
    model = model or config.LLM_MODEL
    api_base = api_base or config.LLM_API_BASE
    api_key = api_key or config.LLM_API_KEY
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    return _do_call(payload, api_base, api_key, timeout, max_retry, attempt=0)


def _do_call(payload, api_base, api_key, timeout, max_retry, attempt) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        f"{api_base}/chat/completions",
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        parsed = json.loads(raw)
        text = parsed["choices"][0]["message"]["content"]
        return {"ok": True, "text": text, "usage": parsed.get("usage", {})}
    except (error.URLError, TimeoutError, ConnectionError) as e:
        if attempt < max_retry:
            time.sleep(2 + attempt * 3)
            return _do_call(payload, api_base, api_key, timeout, max_retry, attempt + 1)
        return {"ok": False, "error": f"net: {e}"}
    except Exception as e:
        if attempt < max_retry and "json" not in str(e).lower():
            time.sleep(1 + attempt * 2)
            return _do_call(payload, api_base, api_key, timeout, max_retry, attempt + 1)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def extract_json(text: str) -> dict:
    """Extract a single JSON object from LLM output."""
    if not text or not text.strip():
        raise ValueError("empty response")
    # Strip markdown code fences
    stripped = re.sub(r"^```(?:json)?\s*", "", text.strip())
    stripped = re.sub(r"\s*```$", "", stripped)
    # Try direct parse first
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    # Try regex extract
    m = re.search(r"\{[\s\S]*\}", stripped)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # Try repairing truncated JSON by adding closing braces
    m = re.search(r"\{[\s\S]*", stripped)
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


def extract_json_array(text: str) -> list:
    """Extract a JSON array from LLM output, with fallback repair for inner quotes."""
    m = re.search(r"\[[\s\S]*\]", text)
    if not m:
        raise ValueError(f"no json array in response: {text[:300]}")
    json_str = m.group(0)
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        repaired = _repair_inner_quotes(json_str)
        return json.loads(repaired)


def _repair_inner_quotes(json_str: str) -> str:
    """Fix bare double quotes inside JSON string values by replacing with corner brackets."""
    out = []
    in_str = False
    i = 0
    n = len(json_str)
    while i < n:
        c = json_str[i]
        if c == "\\" and i + 1 < n:
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
            j = i + 1
            while j < n and json_str[j] in " \t\r\n":
                j += 1
            if j >= n or json_str[j] in ',:}]':
                in_str = False
                out.append(c)
                i += 1
                continue
            out.append("[" if (i == 0 or json_str[i - 1] not in "[[") else "]")
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)
