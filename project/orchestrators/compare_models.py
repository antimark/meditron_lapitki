#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gc
import json
import os
import re
import sys
import tomllib
from copy import deepcopy
from dataclasses import asdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.anonymizer import MedicalTextAnonymizer
from src.config import load_config
from src.io import write_outputs
from src.pipeline import Pipeline
from src.schema import ALL_FIELDS, flatten


def slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return value.strip("_") or "run"


def parse_args():
    p = argparse.ArgumentParser(
        description="Run multiple local/API model profiles and compare their 50-field outputs."
    )
    p.add_argument("folder", help="Folder with .md/.txt test documents")
    p.add_argument("--matrix", default="./config/model_runs.toml", help="Run matrix TOML")
    p.add_argument("--output", default=None, help="Override benchmark.output_dir")
    p.add_argument("--gold", default=None, help="Optional gold result directory/root")
    return p.parse_args()


def resolve_project_path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (ROOT / p).resolve()


def make_run_config(base: dict, run: dict) -> dict:
    cfg = deepcopy(base)
    cfg.setdefault("models", {})
    cfg.setdefault("api", {})
    cfg.setdefault("pipeline", {})
    cfg.setdefault("runtime", {})

    kind = str(run.get("kind", "")).lower()
    if kind not in {"local", "api", "hybrid"}:
        raise ValueError(f"Unsupported run kind: {kind!r}")

    # Start from an explicit isolated run, then enable requested sources.
    cfg["models"]["local_enabled"] = False
    cfg["models"]["local_backend"] = "disabled"
    cfg["api"]["enabled"] = False
    cfg["api"]["mode"] = "disabled"
    cfg["pipeline"]["use_local"] = False
    cfg["pipeline"]["local_mode"] = "disabled"
    cfg["pipeline"]["use_api"] = False

    if kind in {"local", "hybrid"}:
        cfg["models"]["local_enabled"] = True
        cfg["models"]["local_backend"] = run.get("backend", "transformers")
        cfg["models"]["model_alias"] = run.get("model_alias", run.get("model", "qwen_1_5b"))
        cfg["models"]["local_model_path"] = run.get("local_model_path", "")
        for key in (
            "local_base_url", "local_api_key", "local_device", "local_dtype",
            "local_max_new_tokens", "local_temperature", "local_grouped_prompts", "local_max_input_chars",
        ):
            if key in run:
                cfg["models"][key] = run[key]
        cfg["pipeline"]["use_local"] = True
        cfg["pipeline"]["local_mode"] = run.get("local_mode", "all")

    if kind in {"api", "hybrid"}:
        cfg["api"].update({
            "enabled": True,
            "protocol": run.get("protocol", "openai_compatible"),
            "base_url": run.get("base_url", ""),
            "model": run.get("model", ""),
            "api_key": run.get("api_key", ""),
            "api_key_env": run.get("api_key_env", ""),
            "mode": run.get("mode", "all"),
            "timeout_seconds": run.get("timeout_seconds", 90),
            "max_retries": run.get("max_retries", 2),
            "temperature": run.get("temperature", 0.0),
            "max_tokens": run.get("max_tokens", 1200),
            "uncertain_score_below": run.get("uncertain_score_below", 0.88),
            "max_input_chars": run.get("max_input_chars", 14000),
        })
        cfg["pipeline"]["use_api"] = True

    return cfg


def run_dir_name(run: dict) -> str:
    kind = slug(run.get("kind", "run"))
    name = slug(run.get("name", "run"))
    if run.get("kind") == "api":
        return f"{kind}__{name}__{slug(run.get('model', 'model'))}"
    if run.get("kind") == "local":
        return f"{kind}__{name}__{slug(run.get('model_alias', run.get('model', 'model')))}"
    return f"{kind}__{name}"


def locate_gold(gold_root: Path, result_name: str) -> Path | None:
    candidates = [gold_root / "result" / result_name, gold_root / result_name]
    return next((p for p in candidates if p.exists()), None)



def main() -> int:
    a = parse_args()
    folder = Path(a.folder).resolve()
    matrix_path = resolve_project_path(a.matrix)
    matrix = tomllib.loads(matrix_path.read_text(encoding="utf-8"))
    bench = matrix.get("benchmark", {})
    output = Path(a.output).resolve() if a.output else resolve_project_path(bench.get("output_dir", "./benchmark_runs"))
    output.mkdir(parents=True, exist_ok=True)
    comparison_dir = output / "comparison"
    comparison_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(x for x in folder.rglob("*") if x.is_file() and x.suffix.lower() in {".md", ".txt"})
    if not files:
        raise SystemExit("No .md/.txt files found")

    prepared = {}
    anonymize = bool(bench.get("anonymize", False))
    if anonymize:
        anonymizer = MedicalTextAnonymizer(bench.get("anonymizer_mode", "clinical"), bool(bench.get("anonymizer_use_ner", True)))
        anon_dir = output / "_anonymous_inputs"
        report_dir = output / "_anonymization_reports"
        anon_dir.mkdir(parents=True, exist_ok=True)
        report_dir.mkdir(parents=True, exist_ok=True)
        for path in files:
            anon, report = anonymizer.anonymize(path.read_text(encoding="utf-8-sig"))
            anon_name = f"{path.stem}_anon{path.suffix}"
            (anon_dir / anon_name).write_text(anon, encoding="utf-8")
            (report_dir / f"{path.stem}_anon_report.json").write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
            prepared[path.name] = (anon, f"{path.stem}_anon")
    else:
        for path in files:
            prepared[path.name] = (path.read_text(encoding="utf-8-sig"), path.stem)

    base_config_path = resolve_project_path(bench.get("base_config", "./config/regex_only.toml"))
    base_config = load_config(base_config_path)
    runs = [r for r in matrix.get("runs", []) if r.get("enabled", True)]
    if not runs:
        raise SystemExit("No enabled [[runs]] entries in model matrix")

    run_results = {}
    summaries = []
    for run in runs:
        cfg = make_run_config(base_config, run)
        dirname = run_dir_name(run)
        run_out = output / dirname
        run_out.mkdir(parents=True, exist_ok=True)

        if run.get("kind") in {"api", "hybrid"}:
            key = str(run.get("api_key", "") or "")
            env_name = str(run.get("api_key_env", "") or "")
            if not key and not (env_name and os.getenv(env_name)):
                raise SystemExit(f"API run {run.get('name')} has no api_key and env {env_name!r} is empty")
            if not run.get("base_url") or not run.get("model"):
                raise SystemExit(f"API run {run.get('name')} requires base_url and model")

        print(f"=== {dirname} ===")
        pipe = Pipeline(config=cfg)
        if pipe.startup_errors:
            print(json.dumps({"startup_errors": pipe.startup_errors}, ensure_ascii=False), file=sys.stderr)

        results = {}
        quality = []
        for source_name, (text, stem) in prepared.items():
            pack = pipe.run(text, source_name)
            paths = write_outputs(pack, run_out, stem)
            results[source_name] = flatten(pack["result"])
            quality.append(float(pack["score"]["quality_score"]))
            print(f"{source_name}\t{pack['score']['quality_score']:.4f}\t{pack['score']['quality_label']}")
        run_results[dirname] = results
        summaries.append({
            "run": dirname,
            "kind": run.get("kind"),
            "documents": len(results),
            "mean_quality_score": sum(quality) / len(quality) if quality else 0.0,
            "startup_errors": pipe.startup_errors,
        })
        del pipe
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    # Pairwise field agreement between runs.
    pair_rows = []
    for left, right in combinations(run_results.keys(), 2):
        common_docs = sorted(set(run_results[left]) & set(run_results[right]))
        total = matches = 0
        per = {f: [0, 0] for f in ALL_FIELDS}
        for doc in common_docs:
            lrow, rrow = run_results[left][doc], run_results[right][doc]
            for field in ALL_FIELDS:
                same = lrow.get(field) == rrow.get(field)
                total += 1
                matches += int(same)
                per[field][1] += 1
                per[field][0] += int(same)
        pair_rows.append({"left": left, "right": right, "field": "__ALL__", "agreement": matches / total if total else 0.0, "n": total})
        pair_rows.extend({"left": left, "right": right, "field": f, "agreement": m / n if n else 0.0, "n": n} for f, (m, n) in per.items())

    with (comparison_dir / "pairwise_agreement.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["left", "right", "field", "agreement", "n"])
        w.writeheader(); w.writerows(pair_rows)

    # Optional exact match to reviewed gold.
    gold_rows = []
    if a.gold:
        gold_root = Path(a.gold).resolve()
        for run_name, docs in run_results.items():
            total = correct = 0
            per = {f: [0, 0] for f in ALL_FIELDS}
            for source_name, predicted in docs.items():
                stem = prepared[source_name][1]
                gp = locate_gold(gold_root, f"{stem}.json")
                if gp is None and anonymize:
                    # Common gold naming uses original stem even when benchmark input is anonymized.
                    original_stem = Path(source_name).stem
                    gp = locate_gold(gold_root, f"{original_stem}.json")
                if gp is None:
                    continue
                gold = flatten(json.loads(gp.read_text(encoding="utf-8")))
                for f in ALL_FIELDS:
                    same = predicted.get(f) == gold.get(f)
                    total += 1; correct += int(same)
                    per[f][1] += 1; per[f][0] += int(same)
            gold_rows.append({"run": run_name, "field": "__ALL__", "exact_match": correct / total if total else 0.0, "n": total})
            gold_rows.extend({"run": run_name, "field": f, "exact_match": c / n if n else 0.0, "n": n} for f, (c, n) in per.items())

        with (comparison_dir / "gold_exact_match.csv").open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=["run", "field", "exact_match", "n"])
            w.writeheader(); w.writerows(gold_rows)

    summary = {
        "documents": len(files),
        "anonymized": anonymize,
        "runs": summaries,
        "comparison_files": {
            "pairwise_agreement": str(comparison_dir / "pairwise_agreement.csv"),
            "gold_exact_match": str(comparison_dir / "gold_exact_match.csv") if a.gold else None,
        },
    }
    (comparison_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(comparison_dir / "summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
