#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Lightweight local-first de-identification for Russian medical text.

Clinical mode preserves ordinary clinical/event dates but removes direct identifiers,
birth information, age in identity context, and non-clinical biographical quasi-identifiers.
Strict mode additionally removes all exact calendar dates and long standalone numbers.

No network calls are made unless --remote-api-url AND --allow-remote-pii are both set.
The optional spaCy model is loaded only if already installed locally.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str
    source: str
    confidence: float = 1.0


@dataclass
class Finding:
    kind: str
    start: int
    end: int
    text_preview: str
    source: str


@dataclass
class AnonymizationReport:
    mode: str
    ner_enabled: bool
    remote_used: bool
    remote_mode: str
    replacements: Dict[str, int]
    residual_findings: List[Finding]
    safe: bool
    input_sha256: str
    output_sha256: str


MONTHS = (
    r"январ[яе]|феврал[яе]|март[ае]?|апрел[яе]|ма[яе]|июн[яе]|"
    r"июл[яе]|август[ае]?|сентябр[яе]|октябр[яе]|ноябр[яе]|декабр[яе]"
)
YEAR = r"(?:1\d{3}|20\d{2})"
DATE_NUM_PATTERN = rf"(?:0?[1-9]|[12]\d|3[01])[./-](?:0?[1-9]|1[0-2])[./-]{YEAR}"
DATE_WORD_PATTERN = rf"(?:0?[1-9]|[12]\d|3[01])\s+(?:{MONTHS})\s+{YEAR}(?:\s*г(?:ода|\.)?)?"

RE_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-яЁё]{2,}(?![\w-])", re.I)
RE_PHONE = re.compile(r"(?<!\d)(?:\+?7|8)[\s\-()]*(?:\d[\s\-()]*){10}(?!\d)")
RE_SNILS = re.compile(r"(?<!\d)\d{3}[-\s]?\d{3}[-\s]?\d{3}[\s-]?\d{2}(?!\d)")
RE_INN = re.compile(r"(?<!\d)(?:\d{10}|\d{12})(?!\d)")
RE_PASSPORT = re.compile(
    r"(?i)\b(?:паспорт(?:\s+рф)?|серия)\s*[:№]?\s*(?:\d{2}\s?\d{2}|\d{4})\s*(?:№|номер)?\s*\d{6}\b"
)
RE_OMS = re.compile(r"(?i)\b(?:полис(?:\s+омс)?|омс)\s*[:№]?\s*\d{12,16}\b")
RE_BANK_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){15}\d(?!\d)")
RE_IP = re.compile(
    r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?!\d)"
)
RE_USERNAME_LABEL = re.compile(
    r"(?i)\b(?:telegram|телеграм|tg|username|логин|ник)\s*[:\-]?\s*@[A-Za-z0-9_]{4,32}\b"
)
RE_PATIENT_ID = re.compile(
    r"(?i)\b(?:patient\s*id|id\s*пациента|идентификатор\s+пациента|номер\s+пациента)"
    r"\s*[:№\-]?\s*(?P<value>[A-Za-zА-Яа-я0-9/_-]{3,})\b"
)
RE_ADDRESS = re.compile(
    r"(?i)\b(?:адрес(?:\s+(?:регистрации|проживания))?|место\s+жительства)\s*[:\-]\s*[^\n;]{4,160}"
)

# Record number only; labels are preserved. Requiring №/номер/: prevents phrases such as
# "выписка из медицинской карты стационарного больного" from being redacted.
RE_MED_RECORD_1 = re.compile(
    r"(?i)\b(?:истори[яи]\s+болезни|медицинск(?:ая|ой)\s+карт[аы])[ \t]*(?:№|номер|#|:)[ \t]*"
    r"(?P<value>[A-Za-zА-Яа-я0-9/_-]{2,})\b"
)
RE_MED_RECORD_2 = re.compile(
    r"(?i)(?:№|номер|#)\s*(?:истори[яи]\s+болезни|медицинск(?:ой|ая)\s+карт[аы])\s*[:\-]?\s*"
    r"(?P<value>[A-Za-zА-Яа-я0-9/_-]{2,})\b"
)

RE_DATE_NUM = re.compile(rf"(?<!\d){DATE_NUM_PATTERN}(?!\d)", re.I)
RE_DATE_WORD = re.compile(rf"(?<!\d){DATE_WORD_PATTERN}", re.I)

# Exact birth context: the value must directly follow the birth label; no forward search.
RE_DOB_EXACT = re.compile(
    rf"(?i)\b(?:д\.?\s*р\.?|дата\s+рождения|родил(?:ся|ась))\s*[:\-]?\s*(?P<value>{DATE_NUM_PATTERN}|{DATE_WORD_PATTERN})"
)
RE_DOB_YEAR_AFTER_LABEL = re.compile(
    rf"(?i)\b(?:год\s+рождения|г\.?\s*р\.?)\s*[:\-]?\s*(?P<value>{YEAR})\b"
)
RE_DOB_YEAR_BEFORE_LABEL = re.compile(
    rf"(?i)(?P<value>{YEAR})\s*(?:г\.?\s*р\.?|года\s+рождения)\b"
)
RE_CONDITIONAL_BIRTH_YEAR = re.compile(
    rf"(?i)\bусловн(?:ая|о)\s+дата(?:\s+рождения)?\s*[:\-]?\s*(?P<value>{YEAR})(?:\s*г\.?)?"
)

RE_AGE_LABEL = re.compile(
    r"(?i)\bвозраст\s*[:\-]?\s*(?P<value>(?:[1-9]\d{0,2})\s*(?:лет|год(?:а)?))\b"
)
RE_AGE_VALUE = re.compile(r"(?i)\b(?P<value>(?:[1-9]\d{0,2})\s*(?:лет|год(?:а)?))\b")
RE_APPROX_AGE_VALUE = re.compile(r"(?i)\b(?P<value>около\s+(?:[1-9]\d{0,2})\s*(?:лет|год(?:а)?))\b")

# Strongly labelled person names. The label allows a broader capture than the generic
# name patterns, handling synthetic/foreign forms such as "Васко даГамович Индийский".
NAME_LABEL_WORD = r"[А-ЯЁA-Z][А-ЯЁа-яёA-Za-z'’-]{1,30}"
NAME_LABEL_NEXT = r"[А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z'’-]{1,30}"
RE_NAME_LABEL = re.compile(
    rf"(?m)\b(?i:(?:пациент(?:ка)?|фио|ф\.?[ \t]*и\.?[ \t]*о\.?|лечащий[ \t]+врач|"
    rf"направивш(?:ий|ая)[ \t]+врач|врач|доктор|заведующ(?:ий|ая)))[ \t]*[:\-][ \t]*"
    rf"(?P<value>{NAME_LABEL_WORD}(?:[ \t]+{NAME_LABEL_NEXT}){{1,3}})"
)
NAME_TOKEN = r"[А-ЯЁ][а-яё-]{1,30}"
PATRONYMIC = r"[А-ЯЁ][а-яё-]{0,24}(?:ович|евич|ич|овна|евна|ична)"
RE_FIO_GIVEN_FIRST = re.compile(rf"\b{NAME_TOKEN}\s+{PATRONYMIC}\s+{NAME_TOKEN}\b")
RE_FIO_SURNAME_FIRST = re.compile(rf"\b{NAME_TOKEN}\s+{NAME_TOKEN}\s+{PATRONYMIC}\b")
RE_INITIALS = re.compile(
    rf"(?:\b{NAME_TOKEN}\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.(?!\w)|(?<!\w)[А-ЯЁ]\.\s*[А-ЯЁ]\.\s*{NAME_TOKEN}\b)"
)

# Non-clinical biographical quasi-identifiers. Applied only inside an anamnesis-vitae line
# and only to a clause/sentence containing one of these cues.
RE_LIFE_HEADER = re.compile(r"(?im)^[ \t]*анамн?ез[ \t]+жизни[ \t]*:[ \t]*")  # catches "анамнез" and common typo "анамез"
RE_BIO_CUE = re.compile(
    r"(?i)\b(?:работал[аи]?|работает|професси|военн|военнослуж|военачаль|офицер|служб[аеуы]?|"
    r"государственн|общественн|придворн|дипломат|юрист|преподав|исследоват|худож|"
    r"скульптор|музыкант|композитор|пианист|мореплав|экспедиц|поездк|переезд|прожив|"
    r"жил[аи]?\s+за\s+рубеж|верхов(?:ая|ой|ую)|охот|рукопис|издатель|литерат|журналист|"
    r"математ|инженер|изобретател|торговл|картограф|архитектур|сестринск|проповед|"
    r"богослов|астроном|административн|поход|кампан|гастрол|мастерск|общественн(?:ой|ую)?\s+жизн|"
    r"официальн(?:ые|ых|ой)\s+(?:мероприят|приём|поезд)|занимал(?:ся|ась)\s+(?:научн|государственн|"
    r"общественн|преподавательск|исследовательск|литературн|торговл|математическ|естественно-научн)|"
    r"занимал(?:ся|ась)\s+(?:астрономическ|редакционн|придворн)|профессиональн(?:ая|ой)\s+(?:деятельност|нагрузк))"
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def preview(value: str, limit: int = 42) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def regex_spans(text: str, rx: re.Pattern, kind: str, source: str) -> List[Span]:
    return [Span(m.start(), m.end(), kind, source) for m in rx.finditer(text)]


def group_spans(text: str, rx: re.Pattern, group: str, kind: str, source: str) -> List[Span]:
    out: List[Span] = []
    for m in rx.finditer(text):
        a, b = m.span(group)
        if a >= 0 and b > a:
            out.append(Span(a, b, kind, source))
    return out


def identity_age_spans(text: str) -> List[Span]:
    """Redact age only in explicit age fields or patient identity lines.

    Avoids the dangerous global rule that would remove disease durations such as
    "болеет 10 лет".
    """
    out = group_spans(text, RE_AGE_LABEL, "value", "AGE", "age_label")
    offset = 0
    for line in text.splitlines(keepends=True):
        low = line.lower()
        identity_line = (
            "пациент:" in low or "пациентка:" in low or "ф.и.о" in low or "фио:" in low
            or "д.р." in low or "д. р." in low or "дата рождения" in low or "условная дата" in low
        )
        if identity_line:
            for rx in (RE_APPROX_AGE_VALUE, RE_AGE_VALUE):
                for m in rx.finditer(line):
                    a, b = m.span("value")
                    out.append(Span(offset + a, offset + b, "AGE", "identity_line_age"))
        offset += len(line)
    return out


def birth_spans(text: str) -> List[Span]:
    out: List[Span] = []
    for rx, source in [
        (RE_DOB_EXACT, "dob_exact"),
        (RE_DOB_YEAR_AFTER_LABEL, "dob_year_after"),
        (RE_DOB_YEAR_BEFORE_LABEL, "dob_year_before"),
        (RE_CONDITIONAL_BIRTH_YEAR, "dob_conditional"),
    ]:
        out += group_spans(text, rx, "value", "DOB", source)
    return out


def med_record_spans(text: str) -> List[Span]:
    out: List[Span] = []
    out += group_spans(text, RE_MED_RECORD_1, "value", "MED_RECORD", "record_label")
    out += group_spans(text, RE_MED_RECORD_2, "value", "MED_RECORD", "record_prefix")
    return out


def biography_spans(text: str) -> List[Span]:
    """Redact non-clinical biographical clauses in 'Анамнез жизни'.

    Each clause is considered independently so a medical clause after a semicolon can stay.
    The redaction is intentionally limited to the same physical line.
    """
    spans: List[Span] = []
    for hm in RE_LIFE_HEADER.finditer(text):
        line_end = text.find("\n", hm.end())
        if line_end < 0:
            line_end = len(text)
        body_start = hm.end()
        body = text[body_start:line_end]

        # Split while retaining delimiters. Clauses on semicolons are checked separately;
        # sentences on periods are checked separately too.
        pos = 0
        for m in re.finditer(r"[^.;]+[.;]?", body):
            clause = m.group(0)
            content = clause.rstrip(".;").strip()
            if not content:
                continue
            if RE_BIO_CUE.search(content):
                lead = len(clause) - len(clause.lstrip())
                trail = len(clause.rstrip(".;"))
                # Keep punctuation outside the replacement where practical.
                a = body_start + m.start() + lead
                b = body_start + m.start() + trail
                if b > a:
                    spans.append(Span(a, b, "BIO", "life_history_bio"))
    return spans


def merge_spans(spans: Sequence[Span]) -> List[Span]:
    if not spans:
        return []
    spans = sorted(spans, key=lambda s: (s.start, -(s.end - s.start), -s.confidence, s.kind))
    out: List[Span] = []
    for s in spans:
        if s.end <= s.start:
            continue
        if not out or s.start >= out[-1].end:
            out.append(s)
            continue
        prev = out[-1]
        if s.end <= prev.end:
            continue
        out[-1] = Span(prev.start, s.end, prev.kind if prev.kind == s.kind else "PII", f"{prev.source}+{s.source}", max(prev.confidence, s.confidence))
    return out


def apply_spans(text: str, spans: Sequence[Span]) -> Tuple[str, Dict[str, int]]:
    merged = merge_spans(spans)
    counters: Dict[str, int] = {}
    repl: List[Tuple[int, int, str]] = []
    for s in merged:
        counters[s.kind] = counters.get(s.kind, 0) + 1
        repl.append((s.start, s.end, f"<{s.kind}_{counters[s.kind]}>"))
    result = text
    for a, b, token in sorted(repl, reverse=True):
        result = result[:a] + token + result[b:]
    return result, counters


class LocalNER:
    def __init__(self, enabled: bool = True, model_name: str = "ru_core_news_sm"):
        self.enabled = False
        self.nlp = None
        if not enabled:
            return
        try:
            import spacy  # type: ignore
            self.nlp = spacy.load(model_name, exclude=["parser", "lemmatizer", "attribute_ruler"])
            self.enabled = True
        except Exception:
            self.enabled = False
            self.nlp = None

    def person_spans(self, text: str) -> List[Span]:
        if not self.enabled or self.nlp is None:
            return []
        doc = self.nlp(text)
        return [Span(e.start_char, e.end_char, "PERSON", "local_spacy", 0.90) for e in doc.ents if e.label_ in {"PER", "PERSON"}]


class RemotePIIAdapter:
    """Generic future adapter for a separately approved PII-processing service."""
    def __init__(self, url: str, api_key_env: str, allow_remote_pii: bool, allow_http: bool = False, timeout: float = 15.0):
        if not allow_remote_pii:
            raise ValueError("Remote PII transmission is blocked; explicit --allow-remote-pii is required.")
        if not allow_http and not url.lower().startswith("https://"):
            raise ValueError("Remote API must use HTTPS unless --allow-http-api is explicitly set.")
        self.url, self.api_key_env, self.timeout = url, api_key_env, timeout

    def anonymize(self, text: str, mode: str) -> Tuple[Optional[str], List[Span]]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        key = os.getenv(self.api_key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = json.dumps({"text": text, "mode": mode}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(f"Remote PII API request failed: {e}") from e
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            return data["text"], []
        spans: List[Span] = []
        for item in data.get("spans", []) if isinstance(data, dict) else []:
            try:
                spans.append(Span(int(item["start"]), int(item["end"]), str(item.get("kind", "PII")).upper(), "remote_api", float(item.get("confidence", 1.0))))
            except (KeyError, TypeError, ValueError):
                continue
        return None, spans


class MedicalTextAnonymizer:
    def __init__(self, mode: str = "clinical", use_ner: bool = True, ner_model: str = "ru_core_news_sm"):
        if mode not in {"clinical", "strict"}:
            raise ValueError("mode must be clinical or strict")
        self.mode = mode
        self.ner = LocalNER(use_ner, ner_model)

    def detect_local(self, text: str) -> List[Span]:
        s: List[Span] = []
        for rx, kind in [
            (RE_EMAIL, "EMAIL"), (RE_PHONE, "PHONE"), (RE_SNILS, "SNILS"),
            (RE_PASSPORT, "PASSPORT"), (RE_OMS, "OMS"), (RE_BANK_CARD, "LONG_NUMBER"),
            (RE_IP, "IP"), (RE_USERNAME_LABEL, "USERNAME"), (RE_ADDRESS, "ADDRESS"),
        ]:
            s += regex_spans(text, rx, kind, "regex")
        s += group_spans(text, RE_PATIENT_ID, "value", "PATIENT_ID", "patient_id")
        s += med_record_spans(text)

        # Names: labelled capture first, then conservative unlabelled Russian forms.
        s += group_spans(text, RE_NAME_LABEL, "value", "PERSON", "name_label")
        s += regex_spans(text, RE_FIO_GIVEN_FIRST, "PERSON", "fio_given_first")
        s += regex_spans(text, RE_FIO_SURNAME_FIRST, "PERSON", "fio_surname_first")
        s += regex_spans(text, RE_INITIALS, "PERSON", "initials")
        s += self.ner.person_spans(text)

        s += birth_spans(text)
        s += identity_age_spans(text)
        s += biography_spans(text)

        if self.mode == "strict":
            s += regex_spans(text, RE_DATE_NUM, "DATE", "strict_date")
            s += regex_spans(text, RE_DATE_WORD, "DATE", "strict_date")
            s += regex_spans(text, RE_INN, "ID_NUMBER", "strict_long_id")
        return merge_spans(s)

    def validate(self, text: str) -> List[Finding]:
        findings: List[Finding] = []
        checks = [
            ("EMAIL", RE_EMAIL), ("PHONE", RE_PHONE), ("SNILS", RE_SNILS),
            ("PASSPORT", RE_PASSPORT), ("OMS", RE_OMS), ("LONG_NUMBER", RE_BANK_CARD),
            ("IP", RE_IP), ("USERNAME", RE_USERNAME_LABEL), ("ADDRESS", RE_ADDRESS),
            ("PERSON", RE_FIO_GIVEN_FIRST), ("PERSON", RE_FIO_SURNAME_FIRST), ("PERSON", RE_INITIALS),
        ]
        if self.mode == "strict":
            checks += [("DATE", RE_DATE_NUM), ("DATE", RE_DATE_WORD), ("ID_NUMBER", RE_INN)]
        for kind, rx in checks:
            for m in rx.finditer(text):
                findings.append(Finding(kind, m.start(), m.end(), preview(m.group(0)), "validation_regex"))

        # Group-based validators ignore already inserted placeholders because the value regexes
        # accept only ordinary alphanumeric identifiers/dates.
        group_checks = [
            ("PATIENT_ID", RE_PATIENT_ID, "value"),
            ("MED_RECORD", RE_MED_RECORD_1, "value"),
            ("MED_RECORD", RE_MED_RECORD_2, "value"),
            ("PERSON", RE_NAME_LABEL, "value"),
            ("DOB", RE_DOB_EXACT, "value"),
            ("DOB", RE_DOB_YEAR_AFTER_LABEL, "value"),
            ("DOB", RE_DOB_YEAR_BEFORE_LABEL, "value"),
            ("DOB", RE_CONDITIONAL_BIRTH_YEAR, "value"),
        ]
        for kind, rx, group in group_checks:
            for m in rx.finditer(text):
                a, b = m.span(group)
                if a >= 0:
                    findings.append(Finding(kind, a, b, preview(text[a:b]), "validation_context"))

        for s in identity_age_spans(text):
            findings.append(Finding("AGE", s.start, s.end, preview(text[s.start:s.end]), "validation_age"))
        for s in biography_spans(text):
            findings.append(Finding("BIO", s.start, s.end, preview(text[s.start:s.end]), "validation_bio"))
        if self.ner.enabled:
            for s in self.ner.person_spans(text):
                findings.append(Finding("PERSON", s.start, s.end, preview(text[s.start:s.end]), "validation_local_spacy"))

        dedup: Dict[Tuple[int, int, str], Finding] = {}
        for f in findings:
            dedup[(f.start, f.end, f.kind)] = f
        return sorted(dedup.values(), key=lambda x: (x.start, x.end, x.kind))

    def anonymize(
        self,
        text: str,
        remote: Optional[RemotePIIAdapter] = None,
        remote_mode: str = "second-pass",
    ) -> Tuple[str, AnonymizationReport]:
        if remote_mode not in {"second-pass", "primary"}:
            raise ValueError("remote_mode must be second-pass or primary")

        original_hash = sha256_text(text)
        remote_used = remote is not None
        counts: Dict[str, int] = {}

        if remote is not None and remote_mode == "primary":
            # Explicitly approved mode: the separate PII service receives the original text.
            # The local detector still runs afterwards as a safety/fallback pass.
            remote_text, remote_spans = remote.anonymize(text, self.mode)
            if remote_text is not None:
                work = remote_text
                counts["REMOTE_TEXT"] = 1
            else:
                work, rc = apply_spans(text, remote_spans)
                for k, v in rc.items():
                    counts[k] = counts.get(k, 0) + v
            anon, lc = apply_spans(work, self.detect_local(work))
            for k, v in lc.items():
                counts[k] = counts.get(k, 0) + v
        else:
            # Default and safest path: local redaction happens before any optional API call.
            anon, counts = apply_spans(text, self.detect_local(text))
            if remote is not None:
                remote_text, remote_spans = remote.anonymize(anon, self.mode)
                if remote_text is not None:
                    anon = remote_text
                    counts["REMOTE_TEXT"] = counts.get("REMOTE_TEXT", 0) + 1
                elif remote_spans:
                    anon, rc = apply_spans(anon, remote_spans)
                    for k, v in rc.items():
                        counts[k] = counts.get(k, 0) + v

        residual = self.validate(anon)
        report = AnonymizationReport(
            mode=self.mode,
            ner_enabled=self.ner.enabled,
            remote_used=remote_used,
            remote_mode=remote_mode if remote_used else "off",
            replacements=dict(sorted(counts.items())),
            residual_findings=residual,
            safe=not residual,
            input_sha256=original_hash,
            output_sha256=sha256_text(anon),
        )
        return anon, report


def process_file(
    src: Path,
    dst: Optional[Path],
    anonymizer: MedicalTextAnonymizer,
    remote: Optional[RemotePIIAdapter],
    remote_mode: str = "second-pass",
) -> AnonymizationReport:
    text = src.read_text(encoding="utf-8")
    out, report = anonymizer.anonymize(text, remote, remote_mode)
    if dst is None:
        sys.stdout.write(out)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(out, encoding="utf-8")
    return report


def main() -> int:
    p = argparse.ArgumentParser(description="Local-first anonymizer for Russian medical text")
    p.add_argument("input", help="UTF-8 text file, directory, or '-' for stdin")
    p.add_argument("-o", "--output", help="Output file or output directory")
    p.add_argument("--mode", choices=["clinical", "strict"], default="clinical")
    p.add_argument("--no-ner", action="store_true", help="Rules only; minimum memory")
    p.add_argument("--ner-model", default="ru_core_news_sm", help="Already-installed local spaCy model")
    p.add_argument("--report", help="JSON report path")
    p.add_argument("--remote-api-url")
    p.add_argument(
        "--remote-mode",
        choices=["second-pass", "primary"],
        default="second-pass",
        help=(
            "second-pass (default): local anonymization before API; "
            "primary: approved API receives original PII, then local safety pass runs"
        ),
    )
    p.add_argument("--remote-api-key-env", default="PII_API_KEY")
    p.add_argument("--allow-remote-pii", action="store_true")
    p.add_argument("--allow-http-api", action="store_true")
    args = p.parse_args()

    anonymizer = MedicalTextAnonymizer(args.mode, not args.no_ner, args.ner_model)
    remote = None
    if args.remote_api_url:
        remote = RemotePIIAdapter(args.remote_api_url, args.remote_api_key_env, args.allow_remote_pii, args.allow_http_api)

    if args.input == "-":
        text = sys.stdin.read()
        out, rep = anonymizer.anonymize(text, remote, args.remote_mode)
        if args.output:
            Path(args.output).write_text(out, encoding="utf-8")
        else:
            sys.stdout.write(out)
        reports = {"stdin": asdict(rep)}
    else:
        src = Path(args.input)
        if src.is_dir():
            if not args.output:
                p.error("--output directory is required for directory input")
            dst_root = Path(args.output)
            reports = {}
            for f in sorted(x for x in src.rglob("*") if x.is_file() and x.suffix.lower() in {".md", ".txt"} and "__MACOSX" not in x.parts):
                rel = f.relative_to(src)
                rep = process_file(f, dst_root / rel, anonymizer, remote, args.remote_mode)
                reports[str(rel)] = asdict(rep)
        else:
            rep = process_file(src, Path(args.output) if args.output else None, anonymizer, remote, args.remote_mode)
            reports = {src.name: asdict(rep)}

    if args.report:
        Path(args.report).write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")

    unsafe = sum(1 for r in reports.values() if not r["safe"])
    if unsafe:
        print(f"[WARNING] residual PII candidates in {unsafe}/{len(reports)} file(s)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
