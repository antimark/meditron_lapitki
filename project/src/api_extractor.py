from __future__ import annotations
import time
from .llm_common import build_prompt, make_batches, extract_json_object, parse_llm_payload, validate_llm_payload
from .http_llm import openai_compatible_chat


class ApiExtractor:
    def __init__(self, cfg: dict, api_key: str):
        self.cfg = cfg
        self.key = api_key
        self.model = cfg.get("model", "")
        self.base_url = cfg.get("base_url", "")
        if cfg.get("protocol", "openai_compatible") != "openai_compatible":
            raise ValueError("This build supports protocol=openai_compatible; add another adapter in src/http_llm.py if needed.")

    def extract(self, text: str, sections: dict, fields: list[str]):
        merged = {f: [] for f in fields}
        batches = make_batches(text, sections, fields, True, int(self.cfg.get("max_input_chars", 14000)))
        for batch_fields, batch_text in batches:
            err = None
            feedback = None
            for i in range(int(self.cfg.get("max_retries", 2)) + 1):
                try:
                    system, user = build_prompt(batch_text, batch_fields, feedback)
                    raw = openai_compatible_chat(
                        self.base_url, self.key, self.model, system, user,
                        float(self.cfg.get("temperature", 0.0)), int(self.cfg.get("max_tokens", 1200)), int(self.cfg.get("timeout_seconds", 90)),
                    )
                    payload = extract_json_object(raw)
                    validate_llm_payload(payload, batch_text, batch_fields)
                    parsed = parse_llm_payload(payload, text, batch_fields, "api", 0.85)
                    for f in batch_fields:
                        merged[f].extend(parsed.get(f, []))
                    err = None
                    break
                except Exception as e:
                    err = e
                    feedback = str(e)
                    if i < int(self.cfg.get("max_retries", 2)):
                        time.sleep(0.5 * (2 ** i))
            if err is not None:
                raise err
        return merged
