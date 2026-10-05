from __future__ import annotations
import datetime, re
from .schema import NOT_SPEC
from .config import load_json
from .text_utils import value_supported

SPECS = load_json("field_specs.json")


def format_valid(field: str, value: str) -> bool:
    if value == NOT_SPEC:
        return True
    spec = SPECS[field]
    closed = spec.get("closed_values")
    if closed:
        return value in closed
    kind = spec.get("kind")
    if kind == "date":
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", value):
            return False
        try:
            datetime.datetime.strptime(value, "%d.%m.%Y")
            return True
        except ValueError:
            return False
    if kind == "bp":
        m = re.fullmatch(r"(\d{2,3})/(\d{2,3})", value)
        if not m:
            return False
        sys, dia = map(int, m.groups())
        return 50 <= sys <= 300 and 20 <= dia <= 200 and sys >= dia
    if kind == "number":
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
            return False
        rng = spec.get("sanity_range")
        if rng:
            x = float(value)
            return float(rng[0]) <= x <= float(rng[1])
    if field == "diagnosis_icd":
        return bool(re.fullmatch(r"[A-ZА-Я]\d{2}(?:\.\d{1,3})?", value, re.I))
    return True


def evidence_valid(candidate, text: str) -> bool:
    if candidate.meta.get("absence_rule"):
        return True
    return value_supported(candidate.value, candidate.evidence, text)


def cross_validate(flat: dict) -> list[dict]:
    issues = []
    if flat.get("ca_fact") != "Y":
        for f in ("ca_date", "ca_lad", "rca"):
            if flat.get(f) != NOT_SPEC:
                issues.append({"field": f, "issue": "cag_dependent_value_without_performed_cag", "severity": "critical"})
    ad, dd = flat.get("admission_date"), flat.get("discharge_date")
    if ad != NOT_SPEC and dd != NOT_SPEC:
        try:
            a = datetime.datetime.strptime(ad, "%d.%m.%Y")
            d = datetime.datetime.strptime(dd, "%d.%m.%Y")
            if d < a:
                issues.append({"field": "discharge_date", "issue": "discharge_before_admission", "severity": "critical"})
        except ValueError:
            pass
    bp = flat.get("bp")
    if bp and bp != NOT_SPEC and re.fullmatch(r"\d{2,3}/\d{2,3}", bp):
        s, d = map(int, bp.split("/"))
        if s < d:
            issues.append({"field": "bp", "issue": "systolic_below_diastolic", "severity": "critical"})
    return issues
