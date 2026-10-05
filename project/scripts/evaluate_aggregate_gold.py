"""Evaluate pipeline values against one aggregated gold JSON.

Expected gold shape:
{
  "train-0001": {<official 9 groups / 50 fields>},
  "train-0002": {...}
}

This tool intentionally compares values only. It does not treat the supplied gold as
clinically infallible and it does not score evidence spans.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.pipeline import Pipeline
from src.schema import ALL_FIELDS, flatten


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("documents", help="directory containing .md/.txt documents")
    ap.add_argument("gold", help="aggregated JSON keyed by document stem")
    ap.add_argument("--config", default="config/regex_only.toml")
    ap.add_argument("--out", default="validation/aggregate_gold_eval")
    args = ap.parse_args()

    docs = Path(args.documents)
    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pipe = Pipeline(args.config)

    field_stats = {f: {"correct": 0, "total": 0} for f in ALL_FIELDS}
    file_rows = []
    mismatches = []
    evaluated = 0

    for stem, grouped_gold in gold.items():
        candidates = [docs / f"{stem}.md", docs / f"{stem}.txt"]
        doc = next((p for p in candidates if p.exists()), None)
        if doc is None:
            mismatches.append({"file": stem, "field": "__file__", "expected": "present", "actual": "missing"})
            continue

        expected = flatten(grouped_gold)
        actual = flatten(pipe.run(doc.read_text(encoding="utf-8"), doc.name)["result"])
        correct = 0
        for field in ALL_FIELDS:
            e, a = str(expected[field]), str(actual[field])
            field_stats[field]["total"] += 1
            if e == a:
                correct += 1
                field_stats[field]["correct"] += 1
            else:
                mismatches.append({"file": stem, "field": field, "expected": e, "actual": a})
        evaluated += 1
        file_rows.append({"file": stem, "correct": correct, "total": len(ALL_FIELDS), "exact_match": correct / len(ALL_FIELDS)})

    total = sum(x["total"] for x in field_stats.values())
    correct = sum(x["correct"] for x in field_stats.values())
    summary = {
        "documents_in_gold": len(gold),
        "documents_evaluated": evaluated,
        "fields_per_document": len(ALL_FIELDS),
        "field_comparisons": total,
        "exact_value_matches": correct,
        "exact_value_accuracy": (correct / total) if total else 0.0,
        "mismatch_count": len([m for m in mismatches if m["field"] != "__file__"]),
        "note": "Compatibility check against supplied machine labels; not evidence-span scoring and not proof of unseen-set generalization.",
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "mismatches.json").write_text(json.dumps(mismatches, ensure_ascii=False, indent=2), encoding="utf-8")

    with (out / "field_metrics.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["field", "correct", "total", "exact_match"])
        w.writeheader()
        for field, stat in field_stats.items():
            w.writerow({"field": field, **stat, "exact_match": (stat["correct"] / stat["total"]) if stat["total"] else 0.0})
    with (out / "file_metrics.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "correct", "total", "exact_match"])
        w.writeheader(); w.writerows(file_rows)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not mismatches else 2


if __name__ == "__main__":
    raise SystemExit(main())
