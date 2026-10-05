from __future__ import annotations
import json, urllib.request, urllib.error


def openai_compatible_chat(base_url: str, api_key: str, model: str, system: str, user: str, temperature: float = 0.0, max_tokens: int = 1200, timeout: int = 90):
    if not base_url:
        raise ValueError("base_url is empty")
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {e.code}: {detail[:1000]}") from e
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"No choices in LLM response: {str(data)[:1000]}")
    content = choices[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        content = "".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in content)
    return str(content)
