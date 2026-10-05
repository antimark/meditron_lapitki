from __future__ import annotations
from .schema import ALL_FIELDS, NOT_SPEC


def build_context(document_id: str, flat: dict, selected: dict):
    fields = {}
    for f in ALL_FIELDS:
        c = selected.get(f)
        if c is None or flat.get(f) == NOT_SPEC:
            fields[f] = {
                "value": flat.get(f, NOT_SPEC), "source": None,
                "sentence": "", "sentence_start": -1, "sentence_end": -1,
                "evidence_text": "", "evidence_start": -1, "evidence_end": -1,
                "absence_rule": bool(c and c.meta.get("absence_rule")),
            }
            continue
        ev = c.evidence
        fields[f] = {
            "value": flat[f], "source": c.source,
            "sentence": ev.sentence or "", "sentence_start": ev.sentence_start, "sentence_end": ev.sentence_end,
            "evidence_text": ev.text or "", "evidence_start": ev.start, "evidence_end": ev.end,
            "absence_rule": bool(c.meta.get("absence_rule")),
        }
    return {"document_id": document_id, "fields": fields}
