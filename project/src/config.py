from __future__ import annotations
from pathlib import Path
import copy
import json
import os
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]
_ENV_RX = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value):
    """Recursively expand ${VAR} placeholders; missing vars become empty strings."""
    if isinstance(value, str):
        return _ENV_RX.sub(lambda m: os.getenv(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_env(x) for x in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else ROOT / "config" / "default.toml"
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    with open(p, "rb") as fh:
        data = tomllib.load(fh)
    return _expand_env(data)


def clone_config(cfg: dict) -> dict:
    return copy.deepcopy(cfg)


def load_json(name: str):
    return json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))


def api_key(cfg: dict) -> str:
    # Config-file value is supported, but an environment override is preferred for deployment.
    configured = str(cfg.get("api", {}).get("api_key", "") or "")
    env_name = str(cfg.get("api", {}).get("api_key_env", "") or "")
    if env_name and os.getenv(env_name):
        return os.environ[env_name]
    return configured


def resolve_local_model(cfg: dict) -> dict:
    models = load_json("local_models.json")
    mc = cfg.get("models", {})
    alias = mc.get("model_alias", "qwen_1_5b")
    preset = dict(models.get(alias, {}))
    if not preset:
        preset = {"model_id": alias}
    preset["alias"] = alias
    if mc.get("local_model_path"):
        preset["runtime_path"] = mc["local_model_path"]
    else:
        preset["runtime_path"] = preset.get("model_id", alias)
    return preset
