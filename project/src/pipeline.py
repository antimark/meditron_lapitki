"""Hybrid extraction pipeline and routing policy.

The pipeline starts with deterministic rules, optionally adds typo-tolerant and
LLM candidates, validates evidence, arbitrates candidates, and emits result,
context and quality-score artifacts. Local/API LLM providers are lazy and can be
disabled without breaking rules-only processing.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any

from .config import ROOT, api_key, load_config, load_json
from .schema import ALL_FIELDS, FIELD_TO_GROUP, NOT_SPEC, to_nested
from .sections import split_sections
from .regex_extractor import extract_regex
from .local_extractor import LocalExtractor
from .api_extractor import ApiExtractor
from .typo import TypoCorrector
from .arbitration import arbitrate, build_file_score
from .postprocess import postprocess
from .validators import cross_validate, format_valid, evidence_valid
from .context import build_context
from .text_utils import evidence_from_span

THRESHOLDS = load_json("quality_thresholds.json")
SPECS = load_json("field_specs.json")

_DEFAULT_PEER_VALUE_FIELDS = {
    "art_hyper", "atr_fibril", "ckd", "copd", "diagnosis_icd", "dm", "hf", "killip",
    "mi_localisation", "tlt", "type_acs", "ecg_avb", "ecg_elevation", "ecg_rythm",
    "ca_fact", "ca_lad", "rca",
}


class Pipeline:
    def __init__(self, config_path: str | None = None, config: dict | None = None):
        self.cfg = config if config is not None else load_config(config_path)
        runtime = self.cfg.get("runtime", {})
        os.environ["TOKENIZERS_PARALLELISM"] = "true" if runtime.get("hf_tokenizers_parallelism") else "false"
        os.environ["TORCH_NUM_THREADS"] = str(runtime.get("torch_num_threads", 4))

        self.startup_errors: list[dict[str, Any]] = []
        p = self.cfg.get("pipeline", {})
        self.typo = TypoCorrector(ROOT / "config" / "typo_lexicon.txt") if p.get("use_typo_layer", True) else None

        # LLM providers are lazy. A batch that is fully resolved by rules never
        # loads Qwen and never needs an external API connection/key.
        self.local = None
        self.api = None
        self._local_init_attempted = False
        self._api_init_attempted = False
        self._local_init_error: str | None = None
        self._api_init_error: str | None = None

    # ------------------------------------------------------------------
    # Lazy providers: truly optional at runtime.
    # ------------------------------------------------------------------
    def _local_configured(self) -> bool:
        if os.getenv("LOCAL_LLM_ENABLED", "").strip().lower() in {"0", "false", "no", "off"}:
            return False
        p = self.cfg.get("pipeline", {})
        m = self.cfg.get("models", {})
        return bool(
            p.get("use_local", False)
            and m.get("local_enabled", True)
            and m.get("local_backend", "disabled") != "disabled"
            and p.get("local_mode", "uncertain") != "disabled"
        )

    def _api_configured(self) -> bool:
        if os.getenv("LLM_API_ENABLED", "").strip().lower() in {"0", "false", "no", "off"}:
            return False
        p = self.cfg.get("pipeline", {})
        ac = self.cfg.get("api", {})
        key = api_key(self.cfg)
        return bool(
            p.get("use_api", False)
            and ac.get("enabled", False)
            and ac.get("mode", "uncertain") != "disabled"
            and ac.get("model")
            and ac.get("base_url")
            and key
            and key != "PUT_API_KEY_HERE"
        )

    def _get_local(self):
        if self.local is not None:
            return self.local
        if self._local_init_attempted or not self._local_configured():
            return None
        self._local_init_attempted = True
        try:
            self.local = LocalExtractor(self.cfg["models"])
        except Exception as exc:
            self._local_init_error = repr(exc)
            self.startup_errors.append({"source": "local", "stage": "lazy_init", "error": repr(exc)})
            self.local = None
        return self.local

    def _get_api(self):
        if self.api is not None:
            return self.api
        if self._api_init_attempted or not self._api_configured():
            return None
        self._api_init_attempted = True
        ac = self.cfg.get("api", {})
        try:
            self.api = ApiExtractor(ac, api_key(self.cfg))
        except Exception as exc:
            self._api_init_error = repr(exc)
            self.startup_errors.append({"source": "api", "stage": "lazy_init", "error": repr(exc)})
            self.api = None
        return self.api

    def provider_status(self) -> dict[str, Any]:
        return {
            "local_configured": self._local_configured(),
            "local_loaded": self.local is not None,
            "local_init_attempted": self._local_init_attempted,
            "local_error": self._local_init_error,
            "api_configured": self._api_configured(),
            "api_loaded": self.api is not None,
            "api_init_attempted": self._api_init_attempted,
            "api_error": self._api_init_error,
        }

    # ------------------------------------------------------------------
    # Rules / preview.
    # ------------------------------------------------------------------
    def _project_typo_candidates(self, original, shadow, shadow_map, candidates):
        for _, items in candidates.items():
            for c in items:
                c.source = "typo_regex"
                c.confidence = min(c.confidence, 0.86)
                if c.evidence.start >= 0:
                    st = shadow_map[min(c.evidence.start, len(shadow_map) - 1)]
                    en0 = max(c.evidence.end - 1, c.evidence.start)
                    en = shadow_map[min(en0, len(shadow_map) - 1)] + 1
                    c.evidence = evidence_from_span(original, st, min(len(original), en))
                    c.meta["projected_from_typo_shadow"] = True
        return candidates

    def _deterministic_sources(self, text: str, sections: dict):
        sources = {}
        corrections = []
        if self.cfg.get("pipeline", {}).get("use_regex", True):
            sources["regex"] = extract_regex(text, sections)
            if self.typo:
                shadow, corrections, shadow_map = self.typo.correct(text)
                if corrections:
                    typo = extract_regex(shadow, split_sections(shadow))
                    sources["typo_regex"] = self._project_typo_candidates(text, shadow, shadow_map, typo)
        return sources, corrections

    def deterministic_preview(self, text: str) -> dict[str, Any]:
        sections = split_sections(text)
        sources, corrections = self._deterministic_sources(text, sections)
        pconf = self.cfg.get("pipeline", {})
        flat, audit, selected = arbitrate(
            sources,
            text,
            bool(pconf.get("require_exact_llm_evidence", True)),
            float(pconf.get("min_candidate_score", 0.25)),
        )
        flat = postprocess(flat)
        for f in ALL_FIELDS:
            audit[f]["final_value"] = flat[f]
            audit[f]["final_format_valid"] = format_valid(f, flat[f])
        return {"flat": flat, "audit": audit, "selected": selected, "corrections": corrections}

    # ------------------------------------------------------------------
    # Routing helpers.
    # ------------------------------------------------------------------
    @staticmethod
    def _section_present_for_field(field: str, sections: dict, text: str) -> bool:
        spec = SPECS.get(field, {})
        names = spec.get("source_sections") or [spec.get("section")]
        names = [x for x in names if x]
        if "episode" in names:
            return True
        if any(sections.get(name) for name in names):
            return True
        # Discharge medications can be inline and are often not parsed as a heading.
        if FIELD_TO_GROUP.get(field) == "медикаментозная терапия":
            return bool(re.search(
                r"(?i)(назначения\s+при\s+выписке|рекомендации\s+при\s+выписке|"
                r"рекомендовано\s+продолжить|после\s+выписки\s+назначено)", text
            ))
        return False

    def _uncertain_fields(self, audit, threshold, sections=None, text=""):
        out = []
        pconf = self.cfg.get("pipeline", {})
        missing_needs_context = bool(pconf.get("route_missing_only_if_source_section_present", True))
        for f in ALL_FIELDS:
            a = audit.get(f, {})
            if a.get("conflict") or a.get("selected_source") is None or a.get("score", 0.0) < threshold:
                if (
                    missing_needs_context
                    and a.get("selected_source") is None
                    and a.get("reason") == "no_candidate_for_not_specified"
                    and sections is not None
                    and not self._section_present_for_field(f, sections, text)
                ):
                    continue
                out.append(f)
        return out

    @staticmethod
    def _merge_source_candidates(target: dict, incoming: dict) -> dict:
        for field, candidates in (incoming or {}).items():
            target.setdefault(field, []).extend(candidates or [])
        return target

    @staticmethod
    def _valid_family_values(
        sources: dict, family_sources: set[str], field: str, text: str
    ) -> tuple[set[str], int, int]:
        values = set()
        raw_count = valid_count = 0
        for source in family_sources:
            for candidate in sources.get(source, {}).get(field, []):
                if candidate.value == NOT_SPEC:
                    continue
                raw_count += 1
                # Technical absence defaults (binary 0, ca_fact=N, etc.) are not
                # evidence of template coverage. For calibration we compare only
                # direct, text-supported values. A later positive LLM value then
                # correctly appears as a regex_coverage_gap instead of a conflict
                # with a contract default.
                if candidate.meta.get("absence_rule"):
                    continue
                if format_valid(field, candidate.value) and evidence_valid(candidate, text):
                    values.add(candidate.value)
                    valid_count += 1
        return values, raw_count, valid_count

    def _rule_api_comparison(self, sources: dict, text: str) -> dict[str, dict[str, Any]]:
        """Classify rules/API agreement with exact-evidence validation.

        A one-sided API value is intentionally a first-class event: it is the
        signal that train-derived regex coverage may not transfer to this batch.
        """
        events: dict[str, dict[str, Any]] = {}
        for field in ALL_FIELDS:
            rules, rules_raw, rules_valid = self._valid_family_values(
                sources, {"regex", "typo_regex"}, field, text
            )
            api, api_raw, api_valid = self._valid_family_values(sources, {"api"}, field, text)
            if rules and api:
                event = "agree" if rules == api else "semantic_conflict"
            elif not rules and api:
                event = "regex_coverage_gap"
            elif rules and not api:
                event = "api_coverage_gap"
            else:
                event = "both_missing"
            events[field] = {
                "event": event,
                "rules_values": sorted(rules),
                "api_values": sorted(api),
                "rules_raw_candidates": rules_raw,
                "rules_valid_candidates": rules_valid,
                "api_raw_candidates": api_raw,
                "api_valid_candidates": api_valid,
            }
        return events

    @staticmethod
    def _comparison_disagreement_fields(events: dict[str, dict[str, Any]]) -> list[str]:
        return [
            field for field, meta in events.items()
            if meta.get("event") in {"regex_coverage_gap", "api_coverage_gap", "semantic_conflict"}
        ]

    def _peer_arbitration_options(self):
        cfg = self.cfg.get("batch_similarity", {})
        enabled = bool(cfg.get("enabled", False) and cfg.get("use_peer_value_tiebreak", False))
        fields = set(cfg.get("peer_value_fields", []) or _DEFAULT_PEER_VALUE_FIELDS)
        return {
            "weight": float(cfg.get("peer_value_tiebreak_weight", 0.035)) if enabled else 0.0,
            "margin": float(cfg.get("peer_tiebreak_margin", 0.08)),
            "fields": fields,
        }

    # ------------------------------------------------------------------
    # Full run.
    # ------------------------------------------------------------------
    def run(
        self,
        text: str,
        document_id: str = "document",
        routing_policy: dict[str, Any] | None = None,
        peer_prior: dict | None = None,
    ):
        t0 = time.perf_counter()
        source_errors = list(self.startup_errors)
        timings = {}
        sections = split_sections(text)
        pconf = self.cfg.get("pipeline", {})
        routing_policy = routing_policy or {}
        strategy = str(routing_policy.get("strategy", "normal_uncertainty"))

        # 1) deterministic layer + conservative typo shadow.
        ts = time.perf_counter()
        sources, corrections = self._deterministic_sources(text, sections)
        timings["regex_ms"] = round((time.perf_counter() - ts) * 1000, 2)

        preliminary, prelim_audit, _ = arbitrate(
            sources,
            text,
            bool(pconf.get("require_exact_llm_evidence", True)),
            float(pconf.get("min_candidate_score", 0.25)),
        )

        local_fields: list[str] = []
        api_fields: list[str] = []
        disagreement_fields: list[str] = []
        comparison_events: dict[str, dict[str, Any]] = {}

        def call_local(fields: list[str]) -> None:
            nonlocal local_fields
            requested = [f for f in dict.fromkeys(fields) if f not in set(local_fields)]
            if not requested:
                return
            local_provider = self._get_local()
            if local_provider is None:
                return
            ts_local = time.perf_counter()
            try:
                incoming = local_provider.extract(text, sections, requested)
                if "local" not in sources:
                    sources["local"] = {}
                self._merge_source_candidates(sources["local"], incoming)
                local_fields.extend(requested)
            except Exception as exc:
                source_errors.append({"source": "local", "stage": "extract", "error": repr(exc)})
            timings["local_ms"] = round(timings.get("local_ms", 0.0) + (time.perf_counter() - ts_local) * 1000, 2)

        def call_api(fields: list[str]) -> None:
            nonlocal api_fields
            requested = [f for f in dict.fromkeys(fields) if f not in set(api_fields)]
            if not requested:
                return
            api_provider = self._get_api()
            if api_provider is None:
                return
            ts_api = time.perf_counter()
            try:
                incoming = api_provider.extract(text, sections, requested)
                if "api" not in sources:
                    sources["api"] = {}
                self._merge_source_candidates(sources["api"], incoming)
                api_fields.extend(requested)
            except Exception as exc:
                source_errors.append({"source": "api", "stage": "extract", "error": repr(exc)})
            timings["api_ms"] = round(timings.get("api_ms", 0.0) + (time.perf_counter() - ts_api) * 1000, 2)

        # --------------------------------------------------------------
        # Probe documents: rules + API on the same fields. Any mismatch,
        # including a one-sided coverage gap, is sent to the local model.
        # --------------------------------------------------------------
        if strategy in {"probe_compare", "warmup_compare"}:
            api_mode = str(routing_policy.get("api_mode", "all"))
            if api_mode == "all":
                call_api(list(ALL_FIELDS))
            elif api_mode != "disabled":
                call_api(self._uncertain_fields(
                    prelim_audit,
                    float(self.cfg.get("api", {}).get("uncertain_score_below", 0.88)),
                    sections,
                    text,
                ))

            if "api" in sources:
                comparison_events = self._rule_api_comparison(sources, text)
                disagreement_fields = self._comparison_disagreement_fields(comparison_events)

            local_mode = str(routing_policy.get("local_mode", "disagreement"))
            if local_mode in {"disagreement", "conflict"}:
                call_local(disagreement_fields)
            elif local_mode == "all":
                call_local(list(ALL_FIELDS))
            elif local_mode == "uncertain":
                _, pre_local_audit, _ = arbitrate(
                    sources,
                    text,
                    bool(pconf.get("require_exact_llm_evidence", True)),
                    float(pconf.get("min_candidate_score", 0.25)),
                )
                call_local(self._uncertain_fields(
                    pre_local_audit,
                    float(pconf.get("regex_skip_local_score", 0.94)),
                    sections,
                    text,
                ))

            # API is optional. If unavailable, probes degrade to the standard local
            # uncertainty route rather than blocking extraction.
            if "api" not in sources and routing_policy.get("fallback_local_if_api_unavailable", True):
                call_local(self._uncertain_fields(
                    prelim_audit,
                    float(pconf.get("regex_skip_local_score", 0.94)),
                    sections,
                    text,
                ))

        # --------------------------------------------------------------
        # Remaining files: ordinary uncertainty routing plus adaptive expansion
        # learned from the probe subset and batch-wide train/test shift signals.
        # --------------------------------------------------------------
        else:
            local_mode = str(routing_policy.get("local_mode", pconf.get("local_mode", "uncertain")))
            initial_local: list[str] = list(routing_policy.get("local_always_fields", []) or [])
            if local_mode == "all":
                initial_local.extend(ALL_FIELDS)
            elif local_mode != "disabled":
                initial_local.extend(self._uncertain_fields(
                    prelim_audit,
                    float(pconf.get("regex_skip_local_score", 0.94)),
                    sections,
                    text,
                ))
            call_local(initial_local)

            _, pre_api_audit, _ = arbitrate(
                sources,
                text,
                bool(pconf.get("require_exact_llm_evidence", True)),
                float(pconf.get("min_candidate_score", 0.25)),
            )
            ac = self.cfg.get("api", {})
            api_mode = str(routing_policy.get("api_mode", ac.get("mode", "uncertain")))
            requested_api: list[str] = list(routing_policy.get("api_always_fields", []) or [])
            if api_mode == "all":
                requested_api.extend(ALL_FIELDS)
            elif api_mode != "disabled":
                requested_api.extend(self._uncertain_fields(
                    pre_api_audit,
                    float(ac.get("uncertain_score_below", 0.88)),
                    sections,
                    text,
                ))
            call_api(requested_api)

            if "api" in sources:
                comparison_events = self._rule_api_comparison(sources, text)
                disagreement_fields = self._comparison_disagreement_fields(comparison_events)
                # For fields where the probe phase showed regex coverage gaps, API
                # is called broadly. The local model is still reserved for actual
                # current-document disagreements, keeping cost bounded.
                local_on_disagreement = set(routing_policy.get("local_on_api_disagreement_fields", []) or [])
                second_pass = [
                    f for f in disagreement_fields
                    if f in local_on_disagreement and f not in set(local_fields)
                ]
                call_local(second_pass)
            elif routing_policy.get("fallback_local_if_api_unavailable", True):
                # If an adaptive policy requested broader API use but the API is
                # unavailable, the local provider may cover those same fields.
                call_local(list(routing_policy.get("api_always_fields", []) or []))

        # 4) final arbitration + normalization + cross-field validation.
        peer_opts = self._peer_arbitration_options()
        flat, field_audit, selected = arbitrate(
            sources,
            text,
            bool(pconf.get("require_exact_llm_evidence", True)),
            float(pconf.get("min_candidate_score", 0.25)),
            peer_prior=peer_prior,
            peer_prior_weight=peer_opts["weight"],
            peer_tiebreak_margin=peer_opts["margin"],
            peer_value_fields=peer_opts["fields"],
            llm_field_bonus=routing_policy.get("llm_field_bonus", {}),
        )
        flat = postprocess(flat)
        for f in ALL_FIELDS:
            if flat[f] == NOT_SPEC and selected.get(f) is not None:
                selected[f] = None
                field_audit[f]["selected_source"] = None
                field_audit[f]["score"] = 0.0
                field_audit[f]["label"] = "bad"
            field_audit[f]["final_value"] = flat[f]
            field_audit[f]["final_format_valid"] = format_valid(f, flat[f])

        issues = cross_validate(flat)
        file_score, file_label, score_components = build_file_score(flat, field_audit, selected, issues)
        timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        score = {
            "document_id": document_id,
            "quality_score": file_score,
            "base_quality_score": file_score,
            "quality_label": file_label,
            "thresholds_version": THRESHOLDS.get("version"),
            **score_components,
            "field_quality": field_audit,
            "cross_field_issues": issues,
            "typo_corrections": corrections,
            "source_errors": source_errors,
            "routing": {
                "strategy": strategy,
                "local_fields": local_fields,
                "api_fields": api_fields,
                "disagreement_fields": disagreement_fields,
                "comparison_events": comparison_events,
                "local_provider_used": "local" in sources,
                "api_provider_used": "api" in sources,
                "adaptive_cluster": routing_policy.get("adaptive_cluster"),
                "adaptive_policy_ready": routing_policy.get("adaptive_policy_ready", False),
                "adaptive_reasons": routing_policy.get("adaptive_reasons", {}),
                "llm_field_bonus": routing_policy.get("llm_field_bonus", {}),
            },
            "timing_ms": timings,
        }
        context = build_context(document_id, flat, selected)
        return {"result": to_nested(flat), "score": score, "context": context, "flat": flat}
