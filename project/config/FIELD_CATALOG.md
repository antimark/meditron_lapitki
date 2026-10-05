# Field catalog

Generated from `config/field_specs.json`. The official case contract has priority over secondary expert guidance.

| field | group | kind | absence policy | selection policy | closed values |
|---|---|---|---|---|---|
| `admission_date` | даты эпизода | date | `not_spec_if_unmentioned` | `first_valid` |  |
| `discharge_date` | даты эпизода | date | `not_spec_if_unmentioned` | `first_valid` |  |
| `art_hyper` | диагноз | binary | `zero_if_unmentioned` | `first_valid` | 0, 1, не указано |
| `atr_fibril` | диагноз | binary | `zero_if_unmentioned` | `first_valid` | 0, 1, не указано |
| `ckd` | диагноз | text | `not_spec_if_unmentioned` | `first_valid` |  |
| `copd` | диагноз | binary | `zero_if_unmentioned` | `first_valid` | 0, 1, не указано |
| `diagnosis_icd` | диагноз | enum | `not_spec_if_unmentioned` | `first_valid` |  |
| `dm` | диагноз | binary | `zero_if_unmentioned` | `first_valid` | 0, 1, не указано |
| `hf` | диагноз | text | `not_spec_if_unmentioned` | `first_valid` |  |
| `killip` | диагноз | enum | `not_spec_if_unmentioned` | `first_valid` | 1, 2, 3, 4, не указано |
| `mi_localisation` | диагноз | enum | `not_spec_if_unmentioned` | `first_valid` | A, I, L, N, не указано |
| `tlt` | диагноз | binary | `zero_if_unmentioned` | `performed_event_any_section` | 0, 1, не указано |
| `type_acs` | диагноз | enum | `not_spec_if_unmentioned` | `first_valid` | STEMI, NSTEMI, NA, не указано |
| `bmi` | осмотр при поступлении | number | `not_spec_if_unmentioned` | `first_primary_exam` |  |
| `bp` | осмотр при поступлении | bp | `not_spec_if_unmentioned` | `first_primary_exam` |  |
| `bpm` | осмотр при поступлении | number | `not_spec_if_unmentioned` | `first_primary_exam` |  |
| `height` | осмотр при поступлении | number | `not_spec_if_unmentioned` | `first_primary_exam` |  |
| `rr` | осмотр при поступлении | number | `not_spec_if_unmentioned` | `first_primary_exam` |  |
| `smoking` | осмотр при поступлении | text | `not_spec_if_unmentioned` | `first_valid` |  |
| `spo2` | осмотр при поступлении | number | `not_spec_if_unmentioned` | `first_primary_exam` |  |
| `weight` | осмотр при поступлении | number | `not_spec_if_unmentioned` | `first_primary_exam` |  |
| `ecg_avb` | ЭКГ | binary | `zero_if_unmentioned` | `first_ecg_record_binary_presence` | 0, 1, не указано |
| `ecg_bpm` | ЭКГ | number | `not_spec_if_unmentioned` | `first_ecg_record` |  |
| `ecg_elevation` | ЭКГ | binary | `zero_if_unmentioned` | `first_ecg_record` | 0, 1, не указано |
| `ecg_rythm` | ЭКГ | text | `not_spec_if_unmentioned` | `first_ecg_record` |  |
| `echo_ef` | ЭХО-КГ | number | `not_spec_if_unmentioned` | `first_valid` |  |
| `echo_lvd` | ЭХО-КГ | number | `not_spec_if_unmentioned` | `first_valid` |  |
| `echo_lvd_2` | ЭХО-КГ | number | `not_spec_if_unmentioned` | `first_valid` |  |
| `echo_mr` | ЭХО-КГ | text | `not_spec_if_unmentioned` | `first_valid` |  |
| `echo_zone` | ЭХО-КГ | text | `not_spec_if_unmentioned` | `first_valid` |  |
| `rg_date` | рентген грудной полости | date | `not_spec_if_unmentioned` | `first_valid` |  |
| `rg_pc` | рентген грудной полости | text | `not_spec_if_unmentioned` | `first_valid` |  |
| `ca_date` | коронарография | date | `not_spec_if_unmentioned` | `first_valid` |  |
| `ca_fact` | коронарография | enum | `N_if_unmentioned` | `first_valid` | Y, R, N |
| `ca_lad` | коронарография | enum | `not_spec_if_unmentioned` | `maximum_stenosis_for_vessel` | 0, 1, 2, не указано |
| `rca` | коронарография | enum | `not_spec_if_unmentioned` | `maximum_stenosis_for_vessel` | 0, 1, 2, не указано |
| `card_trop` | Лабораторные данные | text | `not_spec_if_unmentioned` | `first_current_admission` |  |
| `crea` | Лабораторные данные | number | `not_spec_if_unmentioned` | `first_current_admission` |  |
| `glu` | Лабораторные данные | number | `not_spec_if_unmentioned` | `first_current_admission` |  |
| `hb` | Лабораторные данные | number | `not_spec_if_unmentioned` | `first_current_admission` |  |
| `ldl` | Лабораторные данные | number | `not_spec_if_unmentioned` | `first_current_admission` |  |
| `leucocytes` | Лабораторные данные | number | `not_spec_if_unmentioned` | `first_current_admission` |  |
| `thrombocytes` | Лабораторные данные | number | `not_spec_if_unmentioned` | `first_current_admission` |  |
| `tot_chol` | Лабораторные данные | number | `not_spec_if_unmentioned` | `first_current_admission` |  |
| `2_aag` | медикаментозная терапия | text | `not_spec_if_unmentioned` | `prefer_discharge_recommendations` |  |
| `ace_ing_sartan` | медикаментозная терапия | text | `not_spec_if_unmentioned` | `prefer_discharge_recommendations` |  |
| `anticoagulant` | медикаментозная терапия | text | `not_spec_if_unmentioned` | `prefer_discharge_recommendations` |  |
| `aspirin` | медикаментозная терапия | text | `not_spec_if_unmentioned` | `prefer_discharge_recommendations` |  |
| `bb` | медикаментозная терапия | text | `not_spec_if_unmentioned` | `prefer_discharge_recommendations` |  |
| `statin` | медикаментозная терапия | text | `not_spec_if_unmentioned` | `prefer_discharge_recommendations` |  |
