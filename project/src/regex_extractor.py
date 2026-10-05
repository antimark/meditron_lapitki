"""Deterministic, evidence-bound extraction for the 50-field case contract.

Most fields are section-scoped. TLT is intentionally handled by a dedicated
case-first extractor because a performed thrombolysis can be documented in
different narrative areas of one epicrisis. Every accepted text-supported
candidate still carries an exact source evidence span.
"""
from __future__ import annotations
import re
from .schema import ALL_FIELDS, NOT_SPEC
from .config import load_json
from .types import Candidate
from .text_utils import evidence_from_span
from .normalizers import (
    norm_date, norm_num, norm_bp, norm_trop, stenosis_code, loc_code, acs_code,
    ROMAN, norm_rhythm, norm_smoking, norm_avb,
)
from .sections import section_regions

PATTERNS = load_json("regex_patterns.json")
VALUES = load_json("field_values.json")
SPECS = load_json("field_specs.json")

NEG = re.compile(
    r"(?i)\b(?:не|нет|без|отсутств\w*|отриц\w*|не\s+выявл\w*|не\s+обнаруж\w*|"
    r"не\s+отмеч\w*|не\s+провод\w*|не\s+выполн\w*|отрица\w*)\b"
)


def _clause(text: str, st: int, en: int) -> str:
    left = max(text.rfind(".", 0, st), text.rfind(";", 0, st), text.rfind("\n", 0, st)) + 1
    rights = [x for x in (text.find(".", en), text.find(";", en), text.find("\n", en)) if x >= 0]
    right = min(rights) if rights else min(len(text), en + 80)
    return text[left:right]


def negated(text: str, st: int, en: int) -> bool:
    ctx = _clause(text, st, en)
    local_start = max(0, st - 45)
    local_end = min(len(text), en + 35)
    local = text[local_start:local_end]
    return bool(NEG.search(local) and NEG.search(ctx))


def _section_for(field: str):
    return SPECS[field].get("section")


def _regions(text, sections, field):
    spec = SPECS[field]
    sec = _section_for(field)
    source_sections = spec.get("source_sections") or ([sec] if sec else [])
    if "episode" in source_sections:
        return [(0, len(text), text)]
    if sec == "meds":
        if sections.get("meds"):
            return sections["meds"]
        return [(0, len(text), text)]

    regs = []
    for name in source_sections:
        regs.extend(sections.get(name, []))
    if regs:
        # Keep document order when a field is legitimately allowed in several sections.
        return sorted(regs, key=lambda r: r[0])
    return section_regions(text, sections, sec)


def _value(field: str, raw, m, original_text: str, abs_st: int, abs_en: int):
    raw_s = "" if raw is None else str(raw)
    kind = SPECS[field].get("kind")
    is_neg = negated(original_text, abs_st, abs_en)
    if kind == "date":
        return norm_date(raw_s)
    if kind == "bp":
        return norm_bp(raw_s)
    if kind == "number":
        return norm_num(raw_s)
    if kind == "binary":
        return "0" if is_neg else "1"
    if field == "ecg_avb":
        return norm_avb(raw_s, m.group(0), is_neg)
    if field == "killip":
        return ROMAN.get(raw_s.upper(), raw_s)
    if field == "mi_localisation":
        return loc_code(raw_s)
    if field == "type_acs":
        return acs_code(raw_s)
    if field in {"ca_lad", "rca"}:
        return stenosis_code(raw_s)
    if field == "diagnosis_icd":
        return raw_s.upper()
    if field == "card_trop":
        return norm_trop(raw_s)
    if field == "ecg_rythm":
        return norm_rhythm(raw_s)
    if field == "smoking":
        return norm_smoking(raw_s)
    if field == "ckd":
        q = raw_s.strip()
        m2 = re.search(r"(?i)(?:ХБП|CKD)\s*([1-5])?\s*([АAБB])?", q)
        if m2:
            tail = "".join(x for x in m2.groups() if x)
            tail = tail.replace("A", "А").replace("B", "Б")
            return "ХБП" + (" " + tail if tail else "")
    return raw_s.strip()


def _confidence(field: str, used_section: bool) -> float:
    simple = {"admission_date", "discharge_date", "bp", "bpm", "rr", "spo2", "height", "weight", "bmi", "ecg_bpm", "echo_ef", "echo_lvd", "echo_lvd_2", "crea", "glu", "hb", "ldl", "leucocytes", "thrombocytes", "tot_chol", "diagnosis_icd"}
    if field in simple:
        return 0.96 if used_section else 0.90
    return 0.93 if used_section else 0.86


def _generic_field(text, sections, field):
    results = []
    for base_st, _, chunk in _regions(text, sections, field):
        used_section = not (base_st == 0 and len(chunk) == len(text))
        for pat in PATTERNS.get(field, []):
            m = re.search(pat, chunk, re.I | re.U)
            if not m:
                continue
            raw = m.group(1) if m.lastindex else m.group(0)
            st, en = base_st + m.start(), base_st + m.end()
            val = _value(field, raw, m, text, st, en)
            if val == NOT_SPEC:
                continue
            results.append(Candidate(field, val, "regex", _confidence(field, used_section), evidence_from_span(text, st, en), str(raw or ""), {"pattern": pat, "section": _section_for(field)}))
            break
        if results:
            break
    return results


def _extract_icd(text, sections):
    candidates = []
    for base_st, _, chunk in _regions(text, sections, "diagnosis_icd"):
        for pat in PATTERNS["diagnosis_icd"]:
            for m in re.finditer(pat, chunk, re.I | re.U):
                raw = m.group(1) if m.lastindex else m.group(0)
                st, en = base_st + m.start(), base_st + m.end()
                near = text[max(base_st, st - 80):min(len(text), en + 40)].lower()
                bonus = 0.04 if "основн" in near else 0.0
                candidates.append(Candidate("diagnosis_icd", str(raw).upper(), "regex", min(0.99, 0.93 + bonus), evidence_from_span(text, st, en), str(raw), {"principal_context": bonus > 0}))
    candidates.sort(key=lambda c: (-c.confidence, c.evidence.start))
    return candidates[:3]


def _extract_tlt(text, sections):
    """Extract performed thrombolytic therapy as a dedicated case-first event.

    Unlike ordinary diagnosis fields, TLT can be documented in treatment, history/course,
    or combined narrative sections such as "Лечение и исход". A positive result therefore
    requires a strong execution clause anywhere in the current epicrisis rather than a bare
    mention inside one predefined section. PCI, stenting, thromboaspiration, plans and
    indications do not count as TLT.
    """
    tlt_event = (
        r"(?:ТЛТ|тромболиз\w*|тромболитическ\w+\s+терап\w*|фибринолиз\w*)"
    )
    thrombolytic = (
        r"(?:альтеплаз\w*|тенектеплаз\w*|стрептокиназ\w*|урокиназ\w*|"
        r"проурокиназ\w*|пуролаз\w*|фортелизин\w*)"
    )
    done = r"(?:провед[её]н(?:а|о|ы)?|выполнен(?:а|о|ы)?|осуществл[её]н(?:а|о|ы)?)"
    administered = r"(?:введ[её]н(?:а|о|ы)?|вводил(?:ся|ась|ось|ись)|введен(?:а|о|ы)?)"

    positive_patterns = [
        rf"\b{done}[^.;\n]{{0,90}}{tlt_event}\b",
        rf"\b{tlt_event}\b[^.;\n]{{0,90}}{done}\b",
        rf"\b(?:{done}|{administered})[^.;\n]{{0,90}}{thrombolytic}\b",
        rf"\b{thrombolytic}\b[^.;\n]{{0,90}}(?:{done}|{administered})\b",
    ]
    hits = []
    for pat in positive_patterns:
        for m in re.finditer(pat, text, re.I | re.U):
            st, en = m.start(), m.end()
            # Guard constructions such as "не проведён тромболизис".
            if negated(text, st, en):
                continue
            hits.append(
                Candidate(
                    "tlt", "1", "regex", 0.99,
                    evidence_from_span(text, st, en),
                    m.group(0),
                    {"extractor": "tlt_case_first", "selection_policy": "performed_event_any_section"},
                )
            )

    if hits:
        hits.sort(key=lambda c: c.evidence.start)
        return [hits[0]]

    negative_patterns = [
        rf"\b{tlt_event}\b[^.;\n]{{0,70}}(?:не\s+(?:проводил\w*|выполнял\w*|проведен\w*|выполнен\w*)|не\s+проводилась|не\s+выполнялась|не\s+проводился|не\s+выполнялся)",
        rf"\bне\s+(?:проводил\w*|выполнял\w*|проведен\w*|выполнен\w*)[^.;\n]{{0,70}}{tlt_event}\b",
    ]
    for pat in negative_patterns:
        m = re.search(pat, text, re.I | re.U)
        if m:
            return [
                Candidate(
                    "tlt", "0", "regex", 0.98,
                    evidence_from_span(text, m.start(), m.end()),
                    m.group(0),
                    {"extractor": "tlt_case_first", "explicit_negative": True},
                )
            ]
    return []

def _extract_cag_fact(text, sections):
    regs = sections.get("cag") or [(0, len(text), text)]
    for base, _, chunk in regs:
        patterns = [
            (r"(?i)отказ\w*[^\n.;]{0,90}(?:коронарограф|КАГ)|(?:коронарограф|КАГ)[^\n.;]{0,90}отказ", "R", 0.99),
            # Case has no separate code for 'not performed for another reason'; map it to N, but keep direct evidence.
            (r"(?i)(?:коронарограф\w*|КАГ)[^\n.;]{0,60}не\s+(?:выполнен\w*|проведен\w*)|не\s+(?:выполнен\w*|проведен\w*)[^\n.;]{0,60}(?:коронарограф\w*|КАГ)", "N", 0.98),
            (r"(?i)(?:выполнен\w*|проведен\w*|проведена)\s+(?:селективн\w+\s+)?(?:коронарограф\w*|КАГ)|(?:КАГ|коронарограф\w*)\s+(?:выполнен\w*|проведен\w*)", "Y", 0.99),
            (r"(?i)coronary\s+angiography\s+(?:performed|done)", "Y", 0.99),
        ]
        for pat, val, conf in patterns:
            m = re.search(pat, chunk)
            if m:
                return [Candidate("ca_fact", val, "regex", conf, evidence_from_span(text, base + m.start(), base + m.end()), m.group(0), {})]
    # Vessel anatomy with actual stenosis description is itself direct evidence of performed CAG.
    m = re.search(r"(?im)^.*(?:ПМЖВ|LAD).*(?:ПКА|RCA).*$", text)
    if m and re.search(r"(?i)стеноз|окклюз|значимого\s+стеноза\s+нет", m.group(0)):
        return [Candidate("ca_fact", "Y", "regex", 0.97, evidence_from_span(text, m.start(), m.end()), m.group(0), {"cag_anatomy": True})]
    return [Candidate("ca_fact", "N", "regex", 0.88, meta={"absence_rule": True, "reason": "no_cag_evidence"})]


def _extract_vessel(text, sections, field):
    regs = sections.get("cag") or [(0, len(text), text)]
    name_pat = {
        "ca_lad": r"(?i)\b(?:ПМЖВ|LAD|передн\w+\s+межжелудочков\w+(?:\s+ветв\w+|\s+артери\w+)?|left\s+anterior\s+descending)\b",
        "rca": r"(?i)\b(?:ПКА|RCA|прав\w+\s+коронарн\w+\s+артери\w*|right\s+coronary\s+artery)\b",
    }[field]
    other_pat = {
        "ca_lad": r"(?i)\b(?:ПКА|RCA|прав\w+\s+коронарн\w+\s+артери\w*)\b",
        "rca": r"(?i)\b(?:ПМЖВ|LAD|передн\w+\s+межжелудочков\w+)\b",
    }[field]
    found = []
    lesion_pat = re.compile(
        r"(?i)(окклюзи\w*|(?:артери\w+\s+)?закрыт\w*|стеноз\w*[^\d%]{0,12}\d{1,3}\s*%|\d{1,3}\s*%|"
        r"(?:гемодинамически\s+)?значимого\s+стеноза\s+нет|без\s+(?:гемодинамически\s+)?значимого\s+стеноза)"
    )
    for base, _, chunk in regs:
        for vm in re.finditer(name_pat, chunk):
            seg_start = vm.start()
            # Stop before the next named target vessel or hard sentence/line delimiter.
            candidates_end = [len(chunk)]
            om = re.search(other_pat, chunk[vm.end():])
            if om: candidates_end.append(vm.end() + om.start())
            for delim in ("\n", ";", "."):
                pos = chunk.find(delim, vm.end())
                if pos >= 0: candidates_end.append(pos)
            seg_end = min(x for x in candidates_end if x > vm.end())
            segment = chunk[seg_start:seg_end]
            for lm in lesion_pat.finditer(segment):
                raw = lm.group(1)
                code = stenosis_code(raw)
                if code == NOT_SPEC: continue
                abs_st = base + seg_start + lm.start()
                abs_en = base + seg_start + lm.end()
                # Include vessel name in evidence so the value cannot be attributed to its neighbor.
                ev_st = base + seg_start
                found.append(Candidate(field, code, "regex", 0.97, evidence_from_span(text, ev_st, abs_en), str(raw), {"selection_policy": "maximum_stenosis_for_vessel"}))
    if not found:
        return []
    max_code = max(int(c.value) for c in found)
    same = [c for c in found if int(c.value) == max_code]
    same.sort(key=lambda c: c.evidence.start)
    return [same[0]]

def _extract_meds(text, sections):
    out = {f: [] for f in VALUES["drug_aliases"]}

    # Discharge medication markers can appear mid-line after another heading, so
    # section splitting alone is not sufficient. Build explicit discharge windows
    # first and never fall back to transient inpatient therapy when such a window exists.
    marker = re.compile(
        r"(?i)(?:назначения\s+при\s+выписке|рекомендации\s+при\s+выписке|"
        r"постоянная\s+терапия\s+при\s+выписке|реком(?:енд|ед)овано\s+продолжить\s+при[её]м|"
        r"после\s+выписки\s+назначено|лекарственная\s+терапия\s+при\s+выписке)"
    )
    discharge_regions = []
    for m in marker.finditer(text):
        st = m.start()
        # A compact window covers the numbered discharge list while limiting leakage
        # into later narrative recommendations.
        en = min(len(text), st + 1800)
        discharge_regions.append((st, en, text[st:en]))

    if discharge_regions:
        regions = discharge_regions
        discharge_mode = True
    elif sections.get("meds"):
        regions = sections["meds"]
        discharge_mode = True
    else:
        regions = []
        discharge_mode = False

    for field, names in VALUES["drug_aliases"].items():
        for base, _, chunk in regions:
            for name in sorted(names, key=len, reverse=True):
                m = re.search(rf"(?i)\b({re.escape(name)}\b[^\n;]{{0,90}})", chunk)
                if m:
                    st, en = base + m.start(), base + m.end()
                    value = m.group(1).strip(" ,.-")
                    out[field] = [Candidate(field, value, "regex", 0.97 if discharge_mode else 0.90, evidence_from_span(text, st, en), value, {"alias": name, "prefer_discharge": discharge_mode})]
                    break
            if out[field]:
                break
    return out


def extract_regex(text, sections):
    out = {f: [] for f in ALL_FIELDS}
    custom = {"diagnosis_icd", "tlt", "ca_fact", "ca_lad", "rca", "2_aag", "ace_ing_sartan", "anticoagulant", "aspirin", "bb", "statin"}
    for field in ALL_FIELDS:
        if field not in custom:
            out[field] = _generic_field(text, sections, field)

    out["diagnosis_icd"] = _extract_icd(text, sections)
    out["tlt"] = _extract_tlt(text, sections)
    out["ca_fact"] = _extract_cag_fact(text, sections)
    out["ca_lad"] = _extract_vessel(text, sections, "ca_lad")
    out["rca"] = _extract_vessel(text, sections, "rca")
    out.update(_extract_meds(text, sections))

    # rg_pc preserves both positive and explicit negative X-ray formulations.

    # Explicit MI with no textual localization => N. Do not infer wall from ECG leads.
    if not out["mi_localisation"]:
        regs = sections.get("diagnosis") or [(0, len(text), text)]
        for base, _, chunk in regs:
            mm = re.search(r"(?i)\b(?:остр\w+\s+)?инфаркт\w*\s+миокард\w*[^\n.]{0,120}", chunk)
            if mm:
                out["mi_localisation"] = [Candidate("mi_localisation", "N", "regex", 0.86, evidence_from_span(text, base + mm.start(), base + mm.end()), mm.group(0), {"coding_rule": "MI_without_named_wall"})]
                break

    # Field-specific absence policy from expert/case config.
    for field, spec in SPECS.items():
        if out[field]:
            continue
        policy = spec.get("absence_policy")
        if policy == "zero_if_unmentioned":
            out[field] = [Candidate(field, "0", "regex", 0.76, meta={"absence_rule": True, "reason": "field_coding_rule"})]
        elif policy == "N_if_unmentioned":
            out[field] = [Candidate(field, "N", "regex", 0.88, meta={"absence_rule": True, "reason": "no_cag_evidence"})]

    # CAG vessel values are only allowed when angiography was performed.
    ca = out["ca_fact"][0].value if out["ca_fact"] else "N"
    if ca != "Y":
        out["ca_lad"] = []
        out["rca"] = []
    return out
