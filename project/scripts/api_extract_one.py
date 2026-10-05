"""Direct API extractor smoke test using [api] from the selected TOML config.
The API key is read from config as requested (or optional api_key_env override).
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import load_config, api_key
from src.api_extractor import ApiExtractor
from src.sections import split_sections
from src.schema import ALL_FIELDS

p = argparse.ArgumentParser()
p.add_argument("file")
p.add_argument("--config", default="config/default.toml")
p.add_argument("--fields", nargs="*", default=None)
a = p.parse_args()
cfg = load_config(a.config)
text = Path(a.file).read_text(encoding="utf-8")
ext = ApiExtractor(cfg["api"], api_key(cfg))
fields = a.fields or ALL_FIELDS
out = ext.extract(text, split_sections(text), fields)
print(json.dumps({f: [c.dict() for c in cs] for f, cs in out.items()}, ensure_ascii=False, indent=2))
