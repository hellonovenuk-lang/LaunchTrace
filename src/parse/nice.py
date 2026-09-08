"""Nice classification helpers."""

from __future__ import annotations

import re

_CLASS_TOKEN = re.compile(r"\b(?:class\s*)?(\d{1,2})\b", re.IGNORECASE)
VALID_CLASSES = set(range(1, 46))


def normalise_classes(values: object) -> list[int]:
    """Coerce whatever a source gives us into a sorted list of valid Nice classes."""
    out: set[int] = set()
    if values is None:
        return []
    if isinstance(values, int):
        candidates: list[str] = [str(values)]
    elif isinstance(values, str):
        candidates = re.split(r"[,;/\s]+", values)
    else:
        try:
            candidates = [str(v) for v in values]  # type: ignore[union-attr]
        except TypeError:  # pragma: no cover - defensive
            return []
    for token in candidates:
        token = token.strip()
        if not token:
            continue
        m = _CLASS_TOKEN.search(token)
        if not m:
            continue
        try:
            n = int(m.group(1))
        except ValueError:
            continue
        if n in VALID_CLASSES:
            out.add(n)
    return sorted(out)


def classes_from_flags(row: dict[str, str]) -> list[int]:
    """Read ``Class1..Class45`` boolean columns (the IPO Open Data shape)."""
    out: list[int] = []
    for n in range(1, 46):
        v = (row.get(f"Class{n}") or "").strip()
        if v and v not in {"0", "", "N", "No", "false", "False"}:
            out.append(n)
    return out
