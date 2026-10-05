from __future__ import annotations
import re
from .config import load_json

ALIASES = load_json("section_aliases.json")


def _header_name(line: str):
    raw = line.strip().lower()
    if not raw:
        return None

    # Many synthetic epicrises use an inline heading at the start of a long line,
    # e.g. "Анализы крови. При поступлении ...". Match the heading prefix before
    # applying the ordinary short-heading guard.
    for name, aliases in ALIASES.items():
        for alias in aliases:
            if re.match(rf"^{re.escape(alias)}(?:\s*[:.;—–-]|\s*$)", raw):
                return name

    h = re.sub(r"[\s:.;—–-]+$", "", raw)
    if not h or len(h) > 120:
        return None
    # Avoid treating ordinary prose with dates as headings.
    if re.search(r"\d{1,2}\.\d{1,2}\.\d{2,4}", h):
        return None
    for name, aliases in ALIASES.items():
        if any(h == a for a in aliases):
            return name
    return None


def _generic_heading_boundary(line: str) -> bool:
    """Return True for an obvious standalone heading not present in ALIASES.

    Unknown headings are used only as boundaries: they terminate the previous
    known clinical section but are not themselves exposed as a source section.
    This prevents a known section from leaking across blocks such as "Прочее:".
    """
    raw = line.strip()
    if not raw or len(raw) > 120:
        return False
    if re.search(r"\d{1,2}\.\d{1,2}\.\d{2,4}", raw):
        return False
    if raw.endswith(":") and len(raw.split()) <= 12:
        return True
    letters = [ch for ch in raw if ch.isalpha()]
    return bool(letters) and len(raw) <= 80 and all(ch.isupper() for ch in letters)


def split_sections(text: str):
    lines = text.splitlines(keepends=True)
    offsets, p = [], 0
    for line in lines:
        offsets.append(p)
        p += len(line)
    marks: list[tuple[int, str | None]] = []
    for i, line in enumerate(lines):
        name = _header_name(line)
        if name:
            marks.append((offsets[i], name))
        elif _generic_heading_boundary(line):
            marks.append((offsets[i], None))

    # Keep every boundary even when its semantic name is unknown. Only known
    # names are returned, but any following boundary closes their region.
    marks = sorted(set(marks), key=lambda x: x[0])
    out: dict[str, list[tuple[int, int, str]]] = {}
    for j, (st, name) in enumerate(marks):
        en = marks[j + 1][0] if j + 1 < len(marks) else len(text)
        if name is not None:
            out.setdefault(name, []).append((st, en, text[st:en]))
    return out


def section_regions(text: str, sections: dict, name: str | None):
    if name and sections.get(name):
        return sections[name]
    return [(0, len(text), text)]
