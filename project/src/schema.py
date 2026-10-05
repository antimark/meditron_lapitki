from __future__ import annotations
from typing import Any

NOT_SPEC = "не указано"
FIELD_GROUPS = {
    "даты эпизода": ["admission_date", "discharge_date"],
    "диагноз": ["art_hyper", "atr_fibril", "ckd", "copd", "diagnosis_icd", "dm", "hf", "killip", "mi_localisation", "tlt", "type_acs"],
    "осмотр при поступлении": ["bmi", "bp", "bpm", "height", "rr", "smoking", "spo2", "weight"],
    "ЭКГ": ["ecg_avb", "ecg_bpm", "ecg_elevation", "ecg_rythm"],
    "ЭХО-КГ": ["echo_ef", "echo_lvd", "echo_lvd_2", "echo_mr", "echo_zone"],
    "рентген грудной полости": ["rg_date", "rg_pc"],
    "коронарография": ["ca_date", "ca_fact", "ca_lad", "rca"],
    "Лабораторные данные": ["card_trop", "crea", "glu", "hb", "ldl", "leucocytes", "thrombocytes", "tot_chol"],
    "медикаментозная терапия": ["2_aag", "ace_ing_sartan", "anticoagulant", "aspirin", "bb", "statin"],
}
ALL_FIELDS = [f for fs in FIELD_GROUPS.values() for f in fs]
FIELD_TO_GROUP = {f: g for g, fs in FIELD_GROUPS.items() for f in fs}
DATE_FIELDS = {"admission_date", "discharge_date", "rg_date", "ca_date"}
NUM_FIELDS = {"bmi", "bpm", "height", "rr", "spo2", "weight", "ecg_bpm", "echo_ef", "echo_lvd", "echo_lvd_2", "crea", "glu", "hb", "ldl", "leucocytes", "thrombocytes", "tot_chol"}
BINARY_FIELDS = {"art_hyper", "atr_fibril", "copd", "dm", "tlt", "ecg_avb", "ecg_elevation"}


def empty_flat():
    return {f: NOT_SPEC for f in ALL_FIELDS}


def to_nested(flat: dict[str, Any]):
    # Case contract requires string values for every field.
    return {g: {f: str(flat.get(f, NOT_SPEC)) for f in fs} for g, fs in FIELD_GROUPS.items()}


def flatten(nested: dict[str, Any]):
    out = empty_flat()
    for g, fs in FIELD_GROUPS.items():
        obj = nested.get(g, {}) if isinstance(nested, dict) else {}
        for f in fs:
            out[f] = str(obj.get(f, NOT_SPEC))
    return out
