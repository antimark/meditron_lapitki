"""Candidate arbitration and file-level quality scoring.

Only candidates produced from the current document may win a field. Evidence,
format validity, source confidence and conflicts are combined conservatively;
peer context cannot create a new value.
"""
from __future__ import annotations
from collections import defaultdict
from .schema import ALL_FIELDS, NOT_SPEC
from .validators import format_valid, evidence_valid
from .config import load_json

THRESHOLDS = load_json("quality_thresholds.json")
SOURCE_WEIGHT = {"regex": 1.00, "typo_regex": 0.92, "local": 0.94, "api": 0.96}
SOURCE_FAMILY = {"regex": "rules", "typo_regex": "rules", "local": "local", "api": "api"}
REGEX_PREF = {
    "admission_date", "discharge_date", "diagnosis_icd", "bmi", "bp", "bpm", "height", "rr", "spo2", "weight",
    "ecg_bpm", "echo_ef", "echo_lvd", "echo_lvd_2", "rg_date", "ca_date", "crea", "glu", "hb", "ldl",
    "leucocytes", "thrombocytes", "tot_chol", "ca_lad", "rca",
}
LLM_PREF = {"hf", "ckd", "smoking", "ecg_rythm", "echo_mr", "echo_zone", "rg_pc"}


def _label_field(score: float, fmt: bool, ev: bool, conflict: bool):
    t = THRESHOLDS["field"]
    if (t.get("force_bad_if_format_invalid") and not fmt) or (t.get("force_bad_if_evidence_invalid") and not ev):
        return "bad"
    if conflict and t.get("force_suspicious_on_conflict"):
        return "suspicious"
    if score >= t["good_min"]:
        return "good"
    if score >= t["suspicious_min"]:
        return "suspicious"
    return "bad"


def arbitrate(
    candidates: dict,
    text: str,
    require_exact_llm_evidence: bool = True,
    min_candidate_score: float = 0.25,
    peer_prior: dict | None = None,
    peer_prior_weight: float = 0.0,
    peer_tiebreak_margin: float = 0.08,
    peer_value_fields: set[str] | None = None,
    llm_field_bonus: dict[str, float] | None = None,
):
    flat, audit, selected = {}, {}, {}
    for field in ALL_FIELDS:
        scored = []
        for src, by_field in candidates.items():
            for c in (by_field.get(field, []) if isinstance(by_field, dict) else []):
                fmt = format_valid(field, c.value)
                ev = evidence_valid(c, text)
                score = float(c.confidence) * SOURCE_WEIGHT.get(c.source, 0.80)
                if not fmt:
                    score *= 0.10
                if not ev:
                    score *= 0.25
                if require_exact_llm_evidence and c.source in {"local", "api"} and not ev and not c.meta.get("absence_rule"):
                    score = min(score, 0.12)
                if field in REGEX_PREF and SOURCE_FAMILY.get(c.source) == "rules":
                    score *= 1.04
                if field in LLM_PREF and c.source in {"local", "api"}:
                    score *= 1.04
                if c.meta.get("absence_rule"):
                    score *= 0.95
                adaptive_llm_bonus = 0.0
                if (
                    llm_field_bonus
                    and c.source in {"local", "api"}
                    and fmt and ev
                    and not c.meta.get("absence_rule")
                ):
                    adaptive_llm_bonus = max(0.0, min(0.05, float(llm_field_bonus.get(field, 0.0) or 0.0)))
                    score = min(1.0, score + adaptive_llm_bonus)
                scored.append({
                    "candidate": c, "score": score, "base_score": score,
                    "format_valid": fmt, "evidence_valid": ev,
                    "peer_support": 0.0, "peer_bonus": 0.0,
                    "adaptive_llm_bonus": adaptive_llm_bonus,
                })

        # Batch peer context is deliberately a weak tie-breaker. It never creates
        # a candidate and cannot rescue a candidate far below the document-local
        # best score. This prevents cohort statistics from becoming clinical truth.
        if scored and peer_prior and peer_prior_weight > 0 and (peer_value_fields is None or field in peer_value_fields):
            best_base = max(x["base_score"] for x in scored)
            field_prior = peer_prior.get(field, {}) if isinstance(peer_prior, dict) else {}
            for x in scored:
                if x["base_score"] < best_base - peer_tiebreak_margin:
                    continue
                support = float(field_prior.get(x["candidate"].value, 0.0) or 0.0)
                bonus = max(0.0, min(peer_prior_weight, peer_prior_weight * support))
                x["peer_support"] = support
                x["peer_bonus"] = bonus
                x["score"] = min(1.0, x["score"] + bonus)

        if not scored:
            flat[field] = NOT_SPEC
            no_score = float(THRESHOLDS["field"].get("no_candidate_score", 0.55))
            audit[field] = {
                "score": no_score, "label": "suspicious", "selected_source": None, "agreement_families": [],
                "conflict": False, "format_valid": True, "evidence_valid": True, "reason": "no_candidate_for_not_specified", "candidates": []
            }
            selected[field] = None
            continue

        # An absence-coded candidate should not beat a supported direct candidate.
        direct_exists = any(x["evidence_valid"] and not x["candidate"].meta.get("absence_rule") and x["score"] >= 0.55 for x in scored)
        if direct_exists:
            for x in scored:
                if x["candidate"].meta.get("absence_rule"):
                    x["score"] *= 0.65

        by_value = defaultdict(list)
        for x in scored:
            by_value[x["candidate"].value].append(x)
        for value, items in by_value.items():
            families = {SOURCE_FAMILY.get(x["candidate"].source, x["candidate"].source) for x in items if x["score"] >= 0.40}
            if len(families) >= 2:
                bonus = min(0.16, 0.08 * (len(families) - 1))
                for x in items:
                    x["score"] = min(1.0, x["score"] + bonus)

        scored.sort(key=lambda x: x["score"], reverse=True)
        viable = [x for x in scored if x["score"] >= min_candidate_score]
        if not viable:
            best = scored[0]
            flat[field] = NOT_SPEC
            selected[field] = None
            conflict = False
            score = 0.0
            fmt = best["format_valid"]
            ev = best["evidence_valid"]
            source = None
            families = []
        else:
            best = viable[0]
            competing = [x for x in viable[1:] if x["candidate"].value != best["candidate"].value and x["score"] >= max(0.55, best["score"] - 0.12)]
            conflict = bool(competing)
            score = best["score"] - (0.12 if conflict else 0.0)
            score = max(0.0, min(1.0, score))
            flat[field] = best["candidate"].value
            selected[field] = best["candidate"]
            fmt, ev = best["format_valid"], best["evidence_valid"]
            source = best["candidate"].source
            families = sorted({SOURCE_FAMILY.get(x["candidate"].source, x["candidate"].source) for x in by_value[best["candidate"].value] if x["score"] >= 0.40})

        label = _label_field(score, fmt, ev, conflict)
        audit[field] = {
            "score": round(score, 4), "label": label, "selected_source": source,
            "agreement_families": families, "agreement_count": len(families),
            "conflict": conflict, "format_valid": fmt, "evidence_valid": ev,
            "candidates": [
                {
                    "source": x["candidate"].source,
                    "source_family": SOURCE_FAMILY.get(x["candidate"].source, x["candidate"].source),
                    "value": x["candidate"].value,
                    "score": round(max(0.0, min(1.0, x["score"])), 4),
                    "format_valid": x["format_valid"], "evidence_valid": x["evidence_valid"],
                    "absence_rule": bool(x["candidate"].meta.get("absence_rule")),
                    "peer_support": round(float(x.get("peer_support", 0.0)), 4),
                    "peer_bonus": round(float(x.get("peer_bonus", 0.0)), 4),
                    "adaptive_llm_bonus": round(float(x.get("adaptive_llm_bonus", 0.0)), 4),
                }
                for x in scored
            ],
        }
    return flat, audit, selected


def build_file_score(flat: dict, field_audit: dict, selected: dict, cross_issues: list[dict]):
    extracted = [f for f, v in flat.items() if v != NOT_SPEC]
    direct = [f for f in extracted if selected.get(f) is not None and selected[f].evidence.start >= 0]
    if not extracted or not direct:
        return 0.0, "bad", {"coverage": len(extracted) / len(ALL_FIELDS), "direct_evidence_fields": len(direct)}
    scores = [field_audit[f]["score"] for f in extracted]
    mean = sum(scores) / len(scores)
    coverage = len(extracted) / len(ALL_FIELDS)
    conflict_fraction = sum(1 for f in extracted if field_audit[f].get("conflict")) / max(1, len(extracted))
    critical = sum(1 for i in cross_issues if i.get("severity") == "critical")
    score = mean * (0.78 + 0.22 * coverage)
    score -= min(0.18, conflict_fraction * 0.30)
    score -= min(0.25, critical * 0.10)
    score = round(max(0.0, min(1.0, score)), 4)
    t = THRESHOLDS["file"]
    if (t.get("force_bad_if_zero_extracted") and not extracted) or (critical and t.get("force_bad_on_critical_validation")):
        label = "bad"
    elif any(field_audit[f].get("conflict") for f in extracted) and t.get("force_suspicious_on_any_high_conflict"):
        label = "suspicious"
    elif score >= t["good_min"]:
        label = "good"
    elif score >= t["suspicious_min"]:
        label = "suspicious"
    else:
        label = "bad"
    return score, label, {
        "coverage": round(coverage, 4), "extracted_fields": len(extracted), "direct_evidence_fields": len(direct),
        "mean_selected_field_score": round(mean, 4), "conflict_fraction": round(conflict_fraction, 4), "critical_issues": critical,
    }


def label_file_score(score: float, field_audit: dict, flat: dict, cross_issues: list[dict]) -> str:
    """Apply the same label gates after optional batch-level score adjustment."""
    extracted = [f for f, v in flat.items() if v != NOT_SPEC]
    critical = sum(1 for i in cross_issues if i.get("severity") == "critical")
    t = THRESHOLDS["file"]
    if (t.get("force_bad_if_zero_extracted") and not extracted) or (critical and t.get("force_bad_on_critical_validation")):
        return "bad"
    if any(field_audit[f].get("conflict") for f in extracted) and t.get("force_suspicious_on_any_high_conflict"):
        return "suspicious"
    if score >= t["good_min"]:
        return "good"
    if score >= t["suspicious_min"]:
        return "suspicious"
    return "bad"
