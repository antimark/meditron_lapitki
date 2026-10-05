from __future__ import annotations
import os, threading
from .config import resolve_local_model
from .llm_common import build_prompt, make_batches, extract_json_object, parse_llm_payload, validate_llm_payload
from .http_llm import openai_compatible_chat


class LocalExtractor:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.backend = cfg.get("local_backend", "disabled")
        self.preset = resolve_local_model({"models": cfg})
        self.model_name = self.preset.get("model_id", cfg.get("model_alias", ""))
        self.runtime_path = self.preset.get("runtime_path", self.model_name)
        self.lock = threading.Lock()
        self.tok = self.model = None
        if self.backend == "transformers":
            import torch
            from transformers import AutoTokenizer, AutoModelForCausalLM
            torch.set_num_threads(int(os.getenv("TORCH_NUM_THREADS", "4")))
            requested = str(cfg.get("local_device", "auto"))
            if requested == "auto":
                if torch.cuda.is_available():
                    self.device = "cuda"
                elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                    self.device = "mps"
                else:
                    self.device = "cpu"
            else:
                self.device = requested
            path = self.runtime_path

            def load_base(model_path):
                kwargs = {"trust_remote_code": True}
                if self.device == "cuda":
                    kwargs.update({"device_map": "auto", "torch_dtype": "auto"})
                    return AutoModelForCausalLM.from_pretrained(model_path, **kwargs)
                if self.device == "mps":
                    kwargs["torch_dtype"] = torch.float16
                    model = AutoModelForCausalLM.from_pretrained(model_path, **kwargs)
                    return model.to("mps")
                kwargs["torch_dtype"] = torch.float32
                return AutoModelForCausalLM.from_pretrained(model_path, **kwargs).to("cpu")

            if os.path.isdir(path) and os.path.exists(os.path.join(path, "adapter_config.json")):
                from peft import PeftConfig, PeftModel
                pcfg = PeftConfig.from_pretrained(path)
                base = pcfg.base_model_name_or_path
                self.tok = AutoTokenizer.from_pretrained(base, trust_remote_code=True)
                base_model = load_base(base)
                self.model = PeftModel.from_pretrained(base_model, path)
            else:
                self.tok = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
                self.model = load_base(path)
            self.model.eval()

    def _one(self, text: str, fields: list[str], validation_feedback: str | None = None):
        system, user = build_prompt(text, fields, validation_feedback)
        if self.backend == "openai_compatible":
            raw = openai_compatible_chat(
                self.cfg.get("local_base_url", ""), self.cfg.get("local_api_key", "EMPTY"), self.model_name,
                system, user, self.cfg.get("local_temperature", 0.0), self.cfg.get("local_max_new_tokens", 1100), 120,
            )
        elif self.backend == "transformers":
            msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            prompt = self.tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
            inputs = self.tok(prompt, return_tensors="pt", truncation=True, max_length=int(self.preset.get("max_length", 4096))).to(next(self.model.parameters()).device)
            with self.lock:
                out = self.model.generate(**inputs, max_new_tokens=int(self.cfg.get("local_max_new_tokens", 1100)), do_sample=False, pad_token_id=self.tok.eos_token_id)
            raw = self.tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        else:
            return {}
        payload = extract_json_object(raw)
        validate_llm_payload(payload, text, fields)
        return payload

    def extract(self, text: str, sections: dict, fields: list[str]):
        merged = {f: [] for f in fields}
        batches = make_batches(text, sections, fields, bool(self.cfg.get("local_grouped_prompts", True)), int(self.cfg.get("local_max_input_chars", 14000)))
        max_retries = int(self.cfg.get("local_max_retries", 1))
        for batch_fields, batch_text in batches:
            feedback = None
            last_error = None
            for _ in range(max_retries + 1):
                try:
                    payload = self._one(batch_text, batch_fields, feedback)
                    parsed = parse_llm_payload(payload, text, batch_fields, "local", 0.82)
                    for f in batch_fields:
                        merged[f].extend(parsed.get(f, []))
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
                    feedback = str(e)
            if last_error is not None:
                raise last_error
        return merged
