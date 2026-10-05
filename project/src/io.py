from __future__ import annotations
from pathlib import Path
import json


def write_outputs(pack: dict, out_dir: str | Path, stem: str):
    out = Path(out_dir)
    result_dir, score_dir, context_dir = out / "result", out / "score", out / "context"
    for d in (result_dir, score_dir, context_dir):
        d.mkdir(parents=True, exist_ok=True)
    result_path = result_dir / f"{stem}.json"
    score_path = score_dir / f"{stem}_score.json"
    context_path = context_dir / f"{stem}_context.json"
    result_path.write_text(json.dumps(pack["result"], ensure_ascii=False, indent=2), encoding="utf-8")
    score_path.write_text(json.dumps(pack["score"], ensure_ascii=False, indent=2), encoding="utf-8")
    context_path.write_text(json.dumps(pack["context"], ensure_ascii=False, indent=2), encoding="utf-8")
    return {"result": str(result_path), "score": str(score_path), "context": str(context_path)}
