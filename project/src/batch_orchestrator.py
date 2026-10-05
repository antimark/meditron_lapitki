"""Batch-level calibration and adaptive routing.

A whole batch is previewed before expensive inference. Representative/diverse
probe documents calibrate which fields need broader LLM checks. Batch signals
change routing and bounded confidence only; they never manufacture clinical facts.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any

from .batch_similarity import BatchSimilarityAnalyzer
from .arbitration import label_file_score
from .config import ROOT
from .schema import ALL_FIELDS, BINARY_FIELDS, NOT_SPEC


class BatchOrchestrator:
    """Adaptive routing for a batch of medical documents.

    Design:
    1. Run deterministic previews and unsupervised clustering on the entire batch.
    2. Select a small representative/diverse probe subset (default: 5 files).
    3. On probes compare rules vs API on all fields; call local LLM only for
       disagreements/coverage gaps.
    4. Derive field-wide and cluster x field routing policy from those probes.
    5. Process the remaining documents with uncertainty routing plus targeted
       expansion where train-derived rules appear not to transfer to the batch.

    Both LLM providers remain optional. If unavailable, the deterministic path
    continues to work and the adaptive policy degrades gracefully.
    """

    def __init__(self, pipeline, cfg: dict | None = None):
        self.pipeline = pipeline
        self.cfg = cfg or pipeline.cfg
        self.ocfg = self.cfg.get("orchestration", {})
        self.similarity = BatchSimilarityAnalyzer(self.cfg.get("batch_similarity", {}))
        self._train_baseline = self._load_train_baseline()

    def _load_train_baseline(self) -> dict[str, Any]:
        path_value = str(self.ocfg.get("train_regex_baseline_file", "config/train_regex_baseline.json") or "")
        if not path_value:
            return {}
        path = Path(path_value)
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def prepare(self, documents: list[dict[str, Any]]) -> dict[str, Any]:
        prepared = []
        for item in documents:
            text = str(item["text"])
            preview = self.pipeline.deterministic_preview(text)
            prepared.append({
                "document_id": str(item["document_id"]),
                "text": text,
                "preview": preview,
            })

        # Clustering/frequency analysis always sees the whole batch before probes
        # are chosen. This is the intended small-batch hackathon architecture.
        analysis = self.similarity.analyze(prepared, sampling_cfg=self.ocfg)
        probe_count = max(0, int(self.ocfg.get("probe_files", self.ocfg.get("warmup_files", 5))))
        selected_ids = list(analysis.get("probe_selection", {}).get("selected_document_ids", []))
        if not selected_ids and probe_count:
            selected_ids = [x["document_id"] for x in prepared[:probe_count]]
        selected_ids = selected_ids[:probe_count]

        id_to_index = {item["document_id"]: idx for idx, item in enumerate(prepared)}
        probe_indices = [id_to_index[x] for x in selected_ids if x in id_to_index]
        processing_order = probe_indices + [i for i in range(len(prepared)) if i not in set(probe_indices)]

        batch_shift = self._batch_regex_shift(prepared)
        return {
            "probe_files": probe_count,
            # Legacy key retained for old UI/code that expects warmup_files.
            "warmup_files": probe_count,
            "probe_document_ids": selected_ids,
            "probe_indices": probe_indices,
            "processing_order": processing_order,
            "probe_selection": analysis.get("probe_selection", {}),
            "similarity": analysis,
            "batch_regex_shift": batch_shift,
            "calibration": {
                "enabled": bool(self.ocfg.get("enabled", True)),
                "probe_results": {},
                "ready": False,
                "completed": 0,
                "expected": len(selected_ids),
                "adaptive_policy": self._policy_from_batch_shift(batch_shift),
            },
            "previews": {
                item["document_id"]: {
                    "flat": item["preview"].get("flat", {}),
                    "audit": item["preview"].get("audit", {}),
                }
                for item in prepared
            },
        }

    def _batch_regex_shift(self, prepared: list[dict[str, Any]]) -> dict[str, Any]:
        baseline_rates = self._train_baseline.get("presence_rate", {}) if self._train_baseline else {}
        if not baseline_rates or len(prepared) < int(self.ocfg.get("batch_shift_min_documents", 5)):
            return {"available": False, "flagged_fields": [], "fields": {}}

        # Coded binary fields are present by contract as 0/1 and are therefore not
        # useful for measuring regex coverage shift.
        fields = [f for f in ALL_FIELDS if f not in BINARY_FIELDS and f != "ca_fact"]
        n = len(prepared)
        current = {
            f: sum(1 for item in prepared if str(item["preview"].get("flat", {}).get(f, NOT_SPEC)) != NOT_SPEC) / n
            for f in fields
        }
        min_baseline = float(self.ocfg.get("batch_shift_min_train_presence", 0.25))
        drop_threshold = float(self.ocfg.get("batch_shift_presence_drop", 0.20))
        flagged = []
        details = {}
        for field in fields:
            if field not in baseline_rates:
                continue
            base = float(baseline_rates[field])
            cur = float(current[field])
            drop = base - cur
            is_flagged = base >= min_baseline and drop >= drop_threshold
            if is_flagged:
                flagged.append(field)
            details[field] = {
                "train_presence": round(base, 4),
                "batch_presence": round(cur, 4),
                "drop": round(drop, 4),
                "flagged": is_flagged,
            }
        return {
            "available": True,
            "baseline_documents": self._train_baseline.get("documents"),
            "batch_documents": n,
            "flagged_fields": flagged,
            "fields": details,
        }

    def _policy_from_batch_shift(self, shift: dict[str, Any]) -> dict[str, Any]:
        global_fields = {}
        if bool(self.ocfg.get("use_batch_shift_for_routing", True)):
            for field in shift.get("flagged_fields", []):
                global_fields[field] = {
                    "mode": "api_always",
                    "reasons": ["batch_regex_coverage_shift"],
                    "metrics": shift.get("fields", {}).get(field, {}),
                }
        return {
            "ready": False,
            "global_fields": global_fields,
            "cluster_fields": {},
            "summary": {
                "expanded_global_fields": sorted(global_fields),
                "expanded_cluster_fields": 0,
                "source": "batch_shift_prepolicy" if global_fields else "default_uncertainty",
            },
        }

    def _probe_routing_policy(self) -> dict[str, Any]:
        return {
            "strategy": "probe_compare",
            "api_mode": str(self.ocfg.get("probe_api_mode", self.ocfg.get("warmup_api_mode", "all"))),
            "local_mode": "disagreement" if bool(self.ocfg.get("probe_local_on_disagreement", True)) else "disabled",
            "fallback_local_if_api_unavailable": bool(self.ocfg.get("fallback_local_if_api_unavailable", True)),
        }

    def routing_policy(
        self,
        index: int,
        document_id: str | None = None,
        prepared: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Legacy fallback when callers do not provide prepared state.
        if not prepared:
            probe_count = max(0, int(self.ocfg.get("probe_files", self.ocfg.get("warmup_files", 5))))
            if bool(self.ocfg.get("enabled", True)) and index < probe_count:
                return self._probe_routing_policy()
            return {
                "strategy": "normal_uncertainty",
                "api_mode": str(self.ocfg.get("normal_api_mode", self.cfg.get("api", {}).get("mode", "uncertain"))),
                "local_mode": str(self.ocfg.get("normal_local_mode", self.cfg.get("pipeline", {}).get("local_mode", "uncertain"))),
            }

        probe_ids = set(prepared.get("probe_document_ids", []))
        if bool(self.ocfg.get("enabled", True)) and document_id in probe_ids:
            return self._probe_routing_policy()

        adaptive = prepared.get("calibration", {}).get("adaptive_policy", {}) or {}
        similarity_meta = prepared.get("similarity", {}).get("documents", {}).get(document_id or "", {})
        cluster = str(similarity_meta.get("cluster", -1))
        global_fields = adaptive.get("global_fields", {}) or {}
        cluster_fields = (adaptive.get("cluster_fields", {}) or {}).get(cluster, {}) or {}

        # Cluster-specific policy overrides the global policy if it requests a
        # stronger route; otherwise they are merged conservatively.
        severity = {"uncertain": 0, "api_always": 1, "both_always": 2}
        merged = {}
        for field in set(global_fields) | set(cluster_fields):
            g = global_fields.get(field, {})
            c = cluster_fields.get(field, {})
            chosen = c if severity.get(c.get("mode", "uncertain"), 0) >= severity.get(g.get("mode", "uncertain"), 0) else g
            reasons = list(dict.fromkeys((g.get("reasons", []) or []) + (c.get("reasons", []) or [])))
            merged[field] = {**chosen, "reasons": reasons}

        api_always = sorted(f for f, p in merged.items() if p.get("mode") in {"api_always", "both_always"})
        local_always = sorted(f for f, p in merged.items() if p.get("mode") == "both_always")
        local_on_disagreement = sorted(f for f, p in merged.items() if p.get("mode") == "api_always")
        llm_bonus = {}
        if bool(self.ocfg.get("adaptive_llm_bonus_enabled", True)):
            api_bonus = float(self.ocfg.get("adaptive_llm_bonus_api_always", 0.015))
            both_bonus = float(self.ocfg.get("adaptive_llm_bonus_both_always", 0.025))
            for field, p in merged.items():
                if p.get("mode") == "api_always":
                    llm_bonus[field] = api_bonus
                elif p.get("mode") == "both_always":
                    llm_bonus[field] = both_bonus

        return {
            "strategy": "adaptive_uncertainty",
            "api_mode": str(self.ocfg.get("normal_api_mode", self.cfg.get("api", {}).get("mode", "uncertain"))),
            "local_mode": str(self.ocfg.get("normal_local_mode", self.cfg.get("pipeline", {}).get("local_mode", "uncertain"))),
            "api_always_fields": api_always,
            "local_always_fields": local_always,
            "local_on_api_disagreement_fields": local_on_disagreement,
            "fallback_local_if_api_unavailable": bool(self.ocfg.get("fallback_local_if_api_unavailable", True)),
            "llm_field_bonus": llm_bonus,
            "adaptive_cluster": int(similarity_meta.get("cluster", -1)),
            "adaptive_policy_ready": bool(adaptive.get("ready", False)),
            "adaptive_reasons": {f: merged[f].get("reasons", []) for f in merged},
        }

    def record_probe_result(self, prepared: dict[str, Any], document_id: str, pack: dict[str, Any]) -> dict[str, Any]:
        """Record one probe result and rebuild routing policy when probes complete."""
        calibration = prepared.setdefault("calibration", {})
        probe_ids = set(prepared.get("probe_document_ids", []))
        if document_id not in probe_ids:
            return calibration

        routing = pack.get("score", {}).get("routing", {}) or {}
        calibration.setdefault("probe_results", {})[document_id] = {
            "api_provider_used": bool(routing.get("api_provider_used")),
            "local_provider_used": bool(routing.get("local_provider_used")),
            "comparison_events": routing.get("comparison_events", {}) or {},
            "disagreement_fields": routing.get("disagreement_fields", []) or [],
        }
        calibration["completed"] = len(calibration["probe_results"])
        calibration["expected"] = len(probe_ids)

        # Recompute after every probe so partial batches still yield a policy; mark
        # ready only after all selected probes have been processed.
        policy = self._build_adaptive_policy(prepared)
        policy["ready"] = calibration["completed"] >= calibration["expected"] and calibration["expected"] > 0
        calibration["ready"] = policy["ready"]
        calibration["adaptive_policy"] = policy
        return calibration

    def _build_adaptive_policy(self, prepared: dict[str, Any]) -> dict[str, Any]:
        results = prepared.get("calibration", {}).get("probe_results", {}) or {}
        doc_meta = prepared.get("similarity", {}).get("documents", {}) or {}
        global_counts: dict[str, Counter] = defaultdict(Counter)
        cluster_counts: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))

        for doc_id, result in results.items():
            if not result.get("api_provider_used"):
                continue
            cluster = str(doc_meta.get(doc_id, {}).get("cluster", -1))
            for field, event_meta in (result.get("comparison_events", {}) or {}).items():
                event = str(event_meta.get("event", "both_missing"))
                global_counts[field]["observations"] += 1
                global_counts[field][event] += 1
                cluster_counts[cluster][field]["observations"] += 1
                cluster_counts[cluster][field][event] += 1

        min_global = max(1, int(self.ocfg.get("adaptive_min_probe_observations_global", 3)))
        min_cluster = max(1, int(self.ocfg.get("adaptive_min_probe_observations_cluster", 1)))
        gap_threshold = float(self.ocfg.get("adaptive_regex_gap_rate", 0.30))
        cluster_gap_threshold = float(self.ocfg.get("adaptive_cluster_regex_gap_rate", 0.50))
        conflict_threshold = float(self.ocfg.get("adaptive_semantic_conflict_rate", 0.20))
        cluster_conflict_threshold = float(self.ocfg.get("adaptive_cluster_semantic_conflict_rate", 0.50))

        def classify(counter: Counter, min_obs: int, gap_t: float, conflict_t: float):
            obs = int(counter.get("observations", 0))
            if obs < min_obs:
                return None
            gap = int(counter.get("regex_coverage_gap", 0))
            conflict = int(counter.get("semantic_conflict", 0))
            api_gap = int(counter.get("api_coverage_gap", 0))
            agree = int(counter.get("agree", 0))
            metrics = {
                "observations": obs,
                "agreement_rate": round(agree / obs, 4),
                "regex_gap_rate": round(gap / obs, 4),
                "semantic_conflict_rate": round(conflict / obs, 4),
                "api_gap_rate": round(api_gap / obs, 4),
            }
            if conflict / obs >= conflict_t:
                return {"mode": "both_always", "reasons": ["probe_semantic_conflict"], "metrics": metrics}
            if gap / obs >= gap_t:
                return {"mode": "api_always", "reasons": ["probe_regex_coverage_gap"], "metrics": metrics}
            return None

        # Start with cheap batch-wide train/test coverage shift signal.
        base_policy = self._policy_from_batch_shift(prepared.get("batch_regex_shift", {}))
        global_fields = dict(base_policy.get("global_fields", {}))
        for field, counter in global_counts.items():
            p = classify(counter, min_global, gap_threshold, conflict_threshold)
            if p:
                if field in global_fields:
                    existing = global_fields[field]
                    severity = {"api_always": 1, "both_always": 2}
                    if severity.get(p["mode"], 0) < severity.get(existing.get("mode"), 0):
                        p["mode"] = existing["mode"]
                    p["reasons"] = list(dict.fromkeys(existing.get("reasons", []) + p.get("reasons", [])))
                    p["metrics"] = {**existing.get("metrics", {}), **p.get("metrics", {})}
                global_fields[field] = p

        cluster_fields = {}
        for cluster, by_field in cluster_counts.items():
            cluster_policy = {}
            for field, counter in by_field.items():
                p = classify(counter, min_cluster, cluster_gap_threshold, cluster_conflict_threshold)
                if p:
                    p["reasons"] = [f"cluster_{cluster}:{x}" for x in p.get("reasons", [])]
                    cluster_policy[field] = p
            if cluster_policy:
                cluster_fields[cluster] = cluster_policy

        return {
            "ready": False,
            "global_fields": global_fields,
            "cluster_fields": cluster_fields,
            "global_probe_stats": {f: dict(c) for f, c in global_counts.items()},
            "cluster_probe_stats": {
                cluster: {f: dict(c) for f, c in by_field.items()}
                for cluster, by_field in cluster_counts.items()
            },
            "summary": {
                "expanded_global_fields": sorted(global_fields),
                "expanded_cluster_fields": sum(len(v) for v in cluster_fields.values()),
                "probes_with_api": sum(1 for r in results.values() if r.get("api_provider_used")),
            },
        }

    def process(self, text: str, document_id: str, index: int, prepared: dict[str, Any] | None = None):
        peer_prior = None
        if prepared:
            peer_prior = prepared.get("similarity", {}).get("peer_priors", {}).get(document_id)
        return self.pipeline.run(
            text,
            document_id,
            routing_policy=self.routing_policy(index, document_id=document_id, prepared=prepared),
            peer_prior=peer_prior,
        )

    def finalize_packs(self, packs_by_document: dict[str, dict], prepared: dict[str, Any] | None):
        """Apply the optional batch-level file score adjustment in memory."""
        if not prepared:
            return packs_by_document
        analysis = prepared.get("similarity", {})
        if not analysis.get("enabled"):
            return packs_by_document
        flats = {doc_id: pack.get("flat", {}) for doc_id, pack in packs_by_document.items()}
        for doc_id, pack in packs_by_document.items():
            score = pack.get("score", {})
            base = float(score.get("base_quality_score", score.get("quality_score", 0.0)))
            consistency = self.similarity.score_consistency(doc_id, flats, analysis)
            adjusted, meta = self.similarity.adjust_score(base, consistency)
            score["base_quality_score"] = round(base, 4)
            score["quality_score"] = adjusted
            score["batch_similarity"] = meta
            score["quality_label"] = label_file_score(
                adjusted, score.get("field_quality", {}), pack.get("flat", {}), score.get("cross_field_issues", [])
            )
        return packs_by_document
