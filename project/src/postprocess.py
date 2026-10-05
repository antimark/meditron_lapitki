from __future__ import annotations
from .schema import ALL_FIELDS, NOT_SPEC
from .normalizers import normalize_field_value


def postprocess(flat: dict):
    out = {}
    for f in ALL_FIELDS:
        v = str(flat.get(f, NOT_SPEC)).strip()
        out[f] = normalize_field_value(f, v) if v != NOT_SPEC else NOT_SPEC
    if out.get("ca_fact") != "Y":
        out["ca_date"] = NOT_SPEC
        out["ca_lad"] = NOT_SPEC
        out["rca"] = NOT_SPEC
    return out
