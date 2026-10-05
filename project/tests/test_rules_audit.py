from pathlib import Path

import pytest

from src.pipeline import Pipeline
from src.schema import flatten, NOT_SPEC
from src.llm_common import validate_llm_payload, LLMContractError

ROOT = Path(__file__).resolve().parents[1]
PIPE = Pipeline(str(ROOT / "config" / "regex_only.toml"))


def values(text: str):
    return flatten(PIPE.run(text, "audit.md")["result"])


def test_inline_headings_are_sectioned_and_first_lab_is_used():
    text = (
        "Период лечения: с 01.02.2024 по 05.02.2024.\n"
        "Анализы крови. При поступлении: креатинин 101 мкмоль/л; глюкоза 5,7 ммоль/л.\n"
        "Контроль перед выпиской: креатинин 78 мкмоль/л; глюкоза 4,8 ммоль/л.\n"
        "ЭКГ при поступлении. Синусовый ритм, ЧСС 81 уд/мин.\n"
    )
    flat = values(text)
    assert flat["admission_date"] == "01.02.2024"
    assert flat["discharge_date"] == "05.02.2024"
    assert flat["crea"] == "101"
    assert flat["glu"] == "5.7"
    assert flat["ecg_bpm"] == "81"
    assert flat["ecg_avb"] == "0"
    assert flat["ecg_elevation"] == "0"


def test_xray_negative_wording_is_preserved_verbatim():
    text = "Рентген ОГК от 04.12.2020. Данных за венозный застой в лёгких не получено."
    flat = values(text)
    assert flat["rg_date"] == "04.12.2020"
    assert flat["rg_pc"] == "Данных за венозный застой в лёгких не получено"


def test_smoking_status_not_replaced_by_generic_former_smoker_label():
    flat = values("Из анамнеза. Прекратила курить. Рекомендован отказ от алкоголя.")
    assert flat["smoking"] == "Прекратила курить"


def test_full_acs_phrase_without_st_elevation_is_nstemi():
    flat = values("Заключительный диагноз. Острый коронарный синдром без подъёма ST, инфаркт миокарда.")
    assert flat["type_acs"] == "NSTEMI"


def test_discharge_medications_override_inpatient_medications():
    text = (
        "Лечение в стационаре. Клопидогрел 300 мг однократно. Аторвастатин 40 мг вечером.\n"
        "Рекомедовано продолжить приём: Клопидогрел 75 мг 1 раз в день; "
        "Аторвастатин 80 мг вечером."
    )
    flat = values(text)
    assert flat["2_aag"] == "Клопидогрел 75 мг 1 раз в день"
    assert flat["statin"] == "Аторвастатин 80 мг вечером"


def test_llm_contract_accepts_coded_absence_without_evidence():
    validate_llm_payload(
        {"ecg_avb": {"value": "0", "evidence": ""}, "ca_fact": {"value": "N", "evidence": ""}},
        "ЭКГ. Синусовый ритм.",
        ["ecg_avb", "ca_fact"],
    )


def test_llm_contract_rejects_direct_value_without_exact_evidence():
    with pytest.raises(LLMContractError):
        validate_llm_payload(
            {"crea": {"value": "98", "evidence": "Креатинин 98"}},
            "Креатинин 101 мкмоль/л",
            ["crea"],
        )
