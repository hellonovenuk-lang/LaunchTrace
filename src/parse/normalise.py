"""Text normalisation shared across parsing, matching and classification."""

from __future__ import annotations

import re
import unicodedata

LEGAL_SUFFIXES = (
    "limited",
    "ltd",
    "ltd.",
    "plc",
    "p.l.c.",
    "llp",
    "l.l.p.",
    "llc",
    "lp",
    "cic",
    "c.i.c.",
    "cio",
    "incorporated",
    "inc",
    "inc.",
    "corporation",
    "corp",
    "corp.",
    "company",
    "co",
    "co.",
    "gmbh",
    "bv",
    "b.v.",
    "nv",
    "n.v.",
    "sarl",
    "s.a.r.l.",
    "srl",
    "s.r.l.",
    "sa",
    "s.a.",
    "ag",
    "as",
    "a/s",
    "oy",
    "ab",
    "aps",
    "pty",
    "pte",
    "sdn bhd",
    "kk",
    "kft",
    "spa",
    "s.p.a.",
    "cyf",
    "cyfyngedig",
    "the",
)

_PUNCT = re.compile(r"[^\w\s&]", re.UNICODE)
_WS = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalise_text(text: str | None) -> str:
    """Lowercase, de-accent, drop punctuation, collapse whitespace."""
    if not text:
        return ""
    t = strip_accents(str(text)).lower()
    t = t.replace("&", " and ")
    t = _PUNCT.sub(" ", t)
    return _WS.sub(" ", t).strip()


def normalise_company_name(name: str | None) -> str:
    """Normalised form used for company matching (legal suffixes removed)."""
    t = normalise_text(name)
    if not t:
        return ""
    tokens = t.split()
    while tokens and tokens[-1] in {s.replace(".", "") for s in LEGAL_SUFFIXES}:
        tokens.pop()
    while tokens and tokens[0] == "the":
        tokens.pop(0)
    return " ".join(tokens)


def company_name_key(name: str | None) -> str:
    """Whitespace-free key for exact-ish index lookups."""
    return normalise_company_name(name).replace(" ", "")


def has_corporate_suffix(name: str | None, suffixes: list[str]) -> bool:
    t = normalise_text(name)
    if not t:
        return False
    tokens = set(t.split())
    return any(s in tokens for s in suffixes)


def token_set(name: str | None) -> set[str]:
    return set(normalise_company_name(name).split())


def similarity(a: str | None, b: str | None) -> float:
    """Jaccard token similarity of two normalised names, 0.0-1.0."""
    ta, tb = token_set(a), token_set(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def summarise(text: str | None, limit: int = 220) -> str | None:
    if not text:
        return None
    t = _WS.sub(" ", str(text)).strip()
    if len(t) <= limit:
        return t
    cut = t[:limit].rsplit(" ", 1)[0]
    return cut + "…"
