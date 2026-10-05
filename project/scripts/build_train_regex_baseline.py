from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.config import ROOT, load_config
from src.pipeline import Pipeline
from src.schema import ALL_FIELDS, NOT_SPEC


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic regex presence baseline from a document folder.")
    parser.add_argument("documents", type=Path)
    parser.add_argument("--config", default=str(ROOT / "config" / "regex_only.toml"))
    parser.add_argument("--out", type=Path, default=ROOT / "config" / "train_regex_baseline.json")
    args = parser.parse_args()

    files = sorted([*args.documents.glob("*.md"), *args.documents.glob("*.txt")])
    if not files:
        raise SystemExit("No .md/.txt files found")
    pipe = Pipeline(config=load_config(args.config))
    counts = {f: 0 for f in ALL_FIELDS}
    direct = {f: 0 for f in ALL_FIELDS}
    extracted_per_doc = []

    for path in files:
        text = path.read_text(encoding="utf-8")
        preview = pipe.deterministic_preview(text)
        flat = preview["flat"]
        audit = preview["audit"]
        extracted = 0
        for field in ALL_FIELDS:
            if str(flat.get(field, NOT_SPEC)) != NOT_SPEC:
                counts[field] += 1
                extracted += 1
                if audit.get(field, {}).get("selected_source") is not None:
                    direct[field] += 1
        extracted_per_doc.append(extracted)

    n = len(files)
    payload = {
        "version": 1,
        "source": "open_train_deterministic_preview",
        "documents": n,
        "presence_rate": {f: round(counts[f] / n, 4) for f in ALL_FIELDS},
        "direct_candidate_rate": {f: round(direct[f] / n, 4) for f in ALL_FIELDS},
        "mean_present_fields": round(sum(extracted_per_doc) / n, 4),
        "min_present_fields": min(extracted_per_doc),
        "max_present_fields": max(extracted_per_doc),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
