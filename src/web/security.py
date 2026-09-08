"""Small security helpers for the public site.

Deliberately minimal: a landing page with one form does not need a framework.
What it does need is not to trust anything a visitor sends.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections import defaultdict

from src.settings import get_settings

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")

# Free-mail domains are accepted but flagged: this is a B2B product and the
# sample is a business document.
FREEMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.uk",
    "hotmail.com",
    "hotmail.co.uk",
    "outlook.com",
    "live.co.uk",
    "icloud.com",
    "aol.com",
    "msn.com",
    "protonmail.com",
    "mail.com",
    "gmx.com",
    "yandex.com",
}
DISPOSABLE_DOMAINS = {
    "mailinator.com",
    "guerrillamail.com",
    "10minutemail.com",
    "tempmail.com",
    "trashmail.com",
    "yopmail.com",
    "sharklasers.com",
    "throwawaymail.com",
    "getnada.com",
    "dispostable.com",
}


def is_valid_work_email(email: str) -> tuple[bool, str | None]:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        return False, "That doesn't look like a valid email address."
    domain = email.rsplit("@", 1)[-1]
    if domain in DISPOSABLE_DOMAINS:
        return False, "Please use a company email address."
    return True, None


def is_freemail(email: str) -> bool:
    return (email or "").strip().lower().rsplit("@", 1)[-1] in FREEMAIL_DOMAINS


def clean_text(value: str | None, max_length: int = 200) -> str:
    """Strip control characters and clamp length. Templates handle escaping."""
    if not value:
        return ""
    cleaned = "".join(ch for ch in str(value) if ch.isprintable())
    return cleaned.strip()[:max_length]


def hash_ip(ip: str | None) -> str | None:
    """Store a salted hash rather than a visitor's IP address."""
    if not ip:
        return None
    salt = get_settings().admin_token or "launchtrace"
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:32]


class RateLimiter:
    """In-process fixed-window limiter. Enough for one form on one instance."""

    def __init__(self, limit: int = 5, window_seconds: int = 3600) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        now = time.time()
        recent = [t for t in self._hits[key] if now - t < self.window]
        self._hits[key] = recent
        if len(recent) >= self.limit:
            return False
        recent.append(now)
        return True


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode(), (b or "").encode())
