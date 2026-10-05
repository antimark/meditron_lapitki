from __future__ import annotations
import datetime, re
from .schema import NOT_SPEC

ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4"}


def norm_date(s):
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})", str(s))
    if not m:
        return NOT_SPEC
    d, mo, y = m.groups()
    y = "20" + y if len(y) == 2 else y
    try:
        datetime.date(int(y), int(mo), int(d))
    except ValueError:
        return NOT_SPEC
    return f"{int(d):02d}.{int(mo):02d}.{int(y):04d}"


def norm_num(s):
    m = re.search(r"-?\d+(?:[.,]\d+)?", str(s))
    return m.group(0).replace(",", ".") if m else NOT_SPEC


def norm_bp(s):
    m = re.search(r"(\d{2,3})\s*[/\\]\s*(\d{2,3})", str(s))
    return f"{m.group(1)}/{m.group(2)}" if m else NOT_SPEC


def norm_trop(s):
    q = str(s).strip().lower()
    if re.search(r"полож|positive|\bpos\b|^\+$", q):
        return "положительный"
    if re.search(r"отриц|negative|\bneg\b|^-$", q):
        return "отрицательный"
    return norm_num(q)


def stenosis_code(s):
    q = str(s)
    if re.search(r"(?i)(?:гемодинамически\s+)?значимого\s+стеноза\s+нет|без\s+(?:гемодинамически\s+)?значимого\s+стеноза", q):
        return "0"
    if re.search(r"(?i)окклюз|тотальн\w+\s+закрыт|артери\w+\s+закрыт", q):
        return "2"
    m = re.search(r"\d{1,3}", q)
    if not m:
        return NOT_SPEC
    x = int(m.group())
    return "0" if x < 50 else ("1" if x < 90 else "2")


def loc_code(s):
    q = str(s).lower().replace("ё", "е")
    if re.search(r"передн", q):
        return "A"
    if re.search(r"нижн|диафрагм", q):
        return "I"
    if re.search(r"боков|латераль", q):
        return "L"
    return "N"


def acs_code(s):
    q = str(s).lower().replace("ё", "е")
    # Case enum is STEMI/NSTEMI/NA. Unstable angina is ACS but is not NSTEMI,
    # so it maps to NA rather than being inferred as NSTEMI.
    if re.search(r"нестабильн\w+\s+стенокард|unstable\s+angina", q):
        return "NA"
    if "nstemi" in q or "имбпst" in q or re.search(r"без\s+подъема\s*st", q):
        return "NSTEMI"
    if "stemi" in q or "импst" in q or re.search(r"с\s+(?:подъемом|элевац\w+)\s*st", q):
        return "STEMI"
    return "NA"


def norm_rhythm(s):
    q = str(s).strip().lower().replace("ё", "е")
    if re.search(r"фибрилляц|atrial\s+fibrillation", q):
        return "фибрилляция предсердий"
    if re.search(r"трепетан|atrial\s+flutter", q):
        return "трепетание предсердий"
    if re.search(r"синусов|\bsinus\b", q):
        return "синусовый"
    if re.search(r"узлов|junctional", q):
        return "узловой"
    if re.search(r"экс|paced", q):
        return "ритм ЭКС"
    if re.search(r"желудочков|ventricular", q):
        return "желудочковый"
    if re.search(r"предсердн|\batrial\b", q):
        return "предсердный"
    return str(s).strip()


def norm_smoking(s):
    raw = str(s).strip().strip(" .;,:-")
    q = raw.lower().replace("ё", "е")
    # Preserve the directly documented status wording where possible: the case
    # expects a text status, not a synthetic clinical category.
    if re.search(r"бросил(?:а)?\s+курить", q):
        return "Бросила курить" if "бросила" in q else "Бросил курить"
    if re.search(r"прекратил(?:а)?\s+курить", q):
        return "Прекратила курить" if "прекратила" in q else "Прекратил курить"
    if re.search(r"бывш\w+\s+курильщик|ex[- ]?smoker", q):
        return "Бывший курильщик"
    if re.search(r"не\s+кур|курение\s+отрица|non[- ]?smoker", q):
        return "Не курит"
    if re.search(r"курит|smoker|табакокур", q):
        return "Курит"
    return raw


def norm_avb(s, whole_match="", negated=False):
    if negated:
        return "0"
    q = f"{s or ''} {whole_match or ''}".strip()
    if re.search(r"(?i)полная", q):
        return "3"
    m = re.search(r"(?i)\b(III|II|I|3|2|1)\b", q)
    if not m:
        return NOT_SPEC
    return ROMAN.get(m.group(1).upper(), m.group(1))


def normalize_field_value(field: str, value: str):
    if value is None:
        return NOT_SPEC
    v = str(value).strip()
    if not v or v.lower() in {"не указано", "null", "none", "n/a"} or (v.lower() == "na" and field != "type_acs"):
        return NOT_SPEC
    if field in {"admission_date", "discharge_date", "rg_date", "ca_date"}:
        return norm_date(v)
    if field == "bp":
        return norm_bp(v)
    if field in {"bmi", "bpm", "height", "rr", "spo2", "weight", "ecg_bpm", "echo_ef", "echo_lvd", "echo_lvd_2", "crea", "glu", "hb", "ldl", "leucocytes", "thrombocytes", "tot_chol"}:
        return norm_num(v)
    if field == "card_trop":
        return norm_trop(v)
    if field == "killip":
        return ROMAN.get(v.upper(), v)
    if field == "mi_localisation":
        return v if v in {"A", "I", "L", "N"} else loc_code(v)
    if field == "type_acs":
        return v.upper() if v.upper() in {"STEMI", "NSTEMI", "NA"} else acs_code(v)
    if field in {"ca_lad", "rca"}:
        return v if v in {"0", "1", "2"} else stenosis_code(v)
    if field == "ca_fact":
        u = v.upper()
        if u in {"Y", "R", "N"}:
            return u
        q = v.lower()
        if "отказ" in q:
            return "R"
        if re.search(r"выполн|проведен|проведена|performed", q):
            return "Y"
        return "N"
    if field == "ecg_avb":
        # Case-first: submission field is binary presence, not AV-block degree.
        if v in {"0", "1"}:
            return v
        if re.search(r"(?i)\b(?:нет|не\s+выяв|отсутств|отриц)", v):
            return "0"
        if re.search(r"(?i)(?:АВ|AV|атриовентрикулярн).*блокад|AV\s+block", v):
            return "1"
        return NOT_SPEC
    if field == "ecg_rythm":
        return norm_rhythm(v)
    if field == "smoking":
        return norm_smoking(v)
    if field in {"art_hyper", "atr_fibril", "copd", "dm", "tlt", "ecg_elevation"}:
        if v in {"0", "1"}:
            return v
        return "0" if re.search(r"(?i)\b(?:нет|не\s+выяв|не\s+провод|отриц|отсутств)", v) else "1"
    return v
