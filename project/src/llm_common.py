from __future__ import annotations
import json, re
from .schema import ALL_FIELDS, FIELD_GROUPS, NOT_SPEC
from .config import load_json, ROOT
from .types import Candidate, Evidence
from .text_utils import evidence_from_span
from .normalizers import normalize_field_value

SPECS = load_json("field_specs.json")
BASE_SYSTEM = (ROOT / "config" / "api_system_prompt.txt").read_text(encoding="utf-8").strip()


class LLMContractError(ValueError):
    pass


def _field_card(field: str) -> dict:
    spec = SPECS[field]
    card = {
        "field": field,
        "source_sections": spec.get("source_sections") or [spec.get("section")],
        "selection": spec.get("selection_policy", "first_valid"),
        "missing": spec.get("absence_policy", "not_spec_if_unmentioned"),
        "instruction": spec.get("guidance", ""),
    }
    if spec.get("case_rule"):
        card["case_override"] = spec["case_rule"]
    if spec.get("guidance_secondary"):
        card["recognition_notes"] = spec["guidance_secondary"]
    if spec.get("closed_values"):
        card["allowed_values"] = spec["closed_values"]
    else:
        card["allowed_values"] = "string normalized according to instruction"
    return card


def build_prompt(text: str, fields: list[str], validation_feedback: str | None = None) -> tuple[str, str]:
    fields = [f for f in fields if f in ALL_FIELDS]
    cards = [_field_card(f) for f in fields]
    skeleton = {f: {"value": NOT_SPEC, "evidence": ""} for f in fields}
    parts = [
        "FIELD_CARDS=",
        json.dumps(cards, ensure_ascii=False, separators=(",", ":")),
        "\nOUTPUT_SHAPE=",
        json.dumps(skeleton, ensure_ascii=False, separators=(",", ":")),
    ]
    if validation_feedback:
        parts.extend([
            "\nPREVIOUS_OUTPUT_REJECTED=",
            validation_feedback[:1200],
            "\nИсправь только нарушения контракта; не ослабляй правила извлечения.",
        ])
    parts.extend(["\nDOCUMENT_BEGIN\n", text, "\nDOCUMENT_END"])
    return BASE_SYSTEM, "".join(parts)


def _best_text_for_group(text: str, sections: dict, group: str, selected_fields: list[str], max_chars: int):
    requested_sections = []
    for field in selected_fields:
        spec = SPECS[field]
        names = spec.get("source_sections") or [spec.get("section")]
        for name in names:
            if name and name not in requested_sections:
                requested_sections.append(name)

    if "episode" in requested_sections:
        s = text
    else:
        regs = []
        seen = set()
        for name in requested_sections:
            for reg in sections.get(name, []):
                key = (reg[0], reg[1])
                if key not in seen:
                    regs.append(reg)
                    seen.add(key)
        regs.sort(key=lambda r: r[0])
        if regs:
            s = "\n".join(r[2] for r in regs)
        elif group == "медикаментозная терапия":
            # Discharge prescriptions are usually near the end and can follow an inline
            # heading that section parsing does not recognize. Prefer an explicit marker.
            marker = re.search(
                r"(?i)(?:назначения\s+при\s+выписке|рекомендации\s+при\s+выписке|"
                r"постоянная\s+терапия\s+при\s+выписке|реком(?:енд|ед)овано\s+продолжить\s+при[её]м|"
                r"после\s+выписки\s+назначено)", text
            )
            s = text[marker.start():] if marker else text[-max_chars:]
        else:
            s = text
    if len(s) > max_chars:
        s = s[:max_chars]
    return s


def make_batches(text: str, sections: dict, fields: list[str], grouped: bool = True, max_chars: int = 14000):
    fields = [f for f in fields if f in ALL_FIELDS]
    if not grouped:
        return [(fields, text[:max_chars])] if fields else []
    out = []
    for group, group_fields in FIELD_GROUPS.items():
        selected = [f for f in group_fields if f in fields]
        if not selected:
            continue

        # Episode-wide fields (currently TLT) are intentionally isolated from the
        # ordinary section-scoped fields in the same clinical group. Otherwise one
        # episode-wide contract would force the complete document into the prompt
        # for every diagnosis field and weaken section boundaries.
        episode_fields = []
        scoped_fields = []
        for field in selected:
            spec = SPECS[field]
            names = spec.get("source_sections") or [spec.get("section")]
            (episode_fields if "episode" in names else scoped_fields).append(field)

        if scoped_fields:
            out.append((scoped_fields, _best_text_for_group(text, sections, group, scoped_fields, max_chars)))
        if episode_fields:
            out.append((episode_fields, text[:max_chars]))
    return out


def extract_json_object(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    a, b = raw.find("{"), raw.rfind("}")
    if a >= 0 and b > a:
        raw = raw[a:b + 1]
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise LLMContractError("top-level JSON must be an object")
    return obj


def validate_llm_payload(payload: dict, original_text: str, fields: list[str]) -> None:
    expected = set(fields)
    actual = set(payload) if isinstance(payload, dict) else set()
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    errors = []
    if missing:
        errors.append("missing keys: " + ", ".join(missing))
    if extra:
        errors.append("extra keys: " + ", ".join(extra))

    from .validators import format_valid

    for field in fields:
        if field not in payload:
            continue
        obj = payload[field]
        if not isinstance(obj, dict):
            errors.append(f"{field}: value must be an object")
            continue
        raw_value = obj.get("value")
        evidence = obj.get("evidence", obj.get("quote"))
        if not isinstance(raw_value, str):
            errors.append(f"{field}: value must be a string")
            continue
        if evidence is None:
            errors.append(f"{field}: evidence key is required")
            continue
        if not isinstance(evidence, str):
            errors.append(f"{field}: evidence must be a string")
            continue

        value = normalize_field_value(field, raw_value)
        if not format_valid(field, value):
            errors.append(f"{field}: invalid normalized value {value!r}")

        policy = SPECS[field].get("absence_policy")
        absence_ok = (
            value == NOT_SPEC
            or (policy == "zero_if_unmentioned" and value == "0")
            or (policy == "N_if_unmentioned" and value == "N")
        )
        if evidence:
            if evidence not in original_text:
                errors.append(f"{field}: evidence is not an exact substring")
        elif not absence_ok:
            errors.append(f"{field}: direct value requires non-empty evidence")

    if errors:
        raise LLMContractError("; ".join(errors))


def _find_quote(text: str, quote: str):
    if not quote:
        return -1
    st = text.find(quote)
    if st >= 0:
        return st
    # Do not fuzzy-map LLM evidence: UI positions must be exact.
    return -1


def parse_llm_payload(payload: dict, original_text: str, fields: list[str], source: str, base_conf: float = 0.80):
    out = {f: [] for f in fields}
    for f in fields:
        obj = payload.get(f, {}) if isinstance(payload, dict) else {}
        if isinstance(obj, dict):
            raw_v = obj.get("value", NOT_SPEC)
            quote = str(obj.get("evidence", obj.get("quote", "")) or "")
        else:
            raw_v, quote = obj, ""
        value = normalize_field_value(f, str(raw_v))
        if value == NOT_SPEC:
            continue
        st = _find_quote(original_text, quote)
        if st >= 0:
            ev = evidence_from_span(original_text, st, st + len(quote))
            exact = True
        else:
            ev = Evidence()
            exact = False
        meta = {"quote_exact": exact}
        policy = SPECS[f].get("absence_policy")
        if not quote and ((policy == "zero_if_unmentioned" and value == "0") or (policy == "N_if_unmentioned" and value == "N")):
            meta.update({"absence_rule": True, "reason": "llm_absence_coding"})
        conf = base_conf if exact or meta.get("absence_rule") else max(0.20, base_conf - 0.35)
        out[f].append(Candidate(f, value, source, conf, ev, str(raw_v), meta))
    return out
