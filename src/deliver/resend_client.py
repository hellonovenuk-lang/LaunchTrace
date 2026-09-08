"""Transactional email via Resend.

If ``RESEND_API_KEY`` is absent the client runs in *file mode*: every message is
written to ``reports/outbox/`` exactly as it would have been sent, and reported
as ``rendered_not_sent``.  Nothing silently disappears and nothing is
accidentally sent.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.deliver.email_render import RenderedEmail
from src.errors import ProviderError, RateLimitedError
from src.logging_setup import get_logger
from src.settings import REPORTS_DIR, Settings, get_settings

log = get_logger(__name__)

RESEND_URL = "https://api.resend.com/emails"
OUTBOX = REPORTS_DIR / "outbox"


@dataclass
class SendResult:
    status: str  # sent | rendered_not_sent | failed
    message_id: str | None = None
    path: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"sent", "rendered_not_sent"}


class EmailSender:
    def __init__(self, settings: Settings | None = None, outbox: Path | None = None) -> None:
        self.settings = settings or get_settings()
        self.outbox = outbox or OUTBOX

    @property
    def live(self) -> bool:
        return bool(self.settings.resend_api_key)

    def send(
        self,
        to: list[str],
        rendered: RenderedEmail,
        attachments: list[Path] | None = None,
        idempotency_key: str | None = None,
    ) -> SendResult:
        if not to:
            return SendResult(status="failed", error="no recipients")
        payload: dict[str, Any] = {
            "from": self.settings.email_from,
            "to": to,
            "subject": rendered.subject,
            "html": rendered.html,
            "text": rendered.text,
        }
        if self.settings.email_reply_to:
            payload["reply_to"] = self.settings.email_reply_to
        if attachments:
            payload["attachments"] = [
                {
                    "filename": p.name,
                    "content": base64.b64encode(p.read_bytes()).decode("ascii"),
                }
                for p in attachments
                if p.exists()
            ]

        if not self.live:
            return self._write_to_outbox(to, rendered, payload, attachments)

        try:
            message_id = self._post(payload, idempotency_key)
        except Exception as exc:
            log.error("email.send_failed", to=to, error=str(exc)[:300])
            return SendResult(status="failed", error=str(exc)[:400])
        log.info("email.sent", to=to, subject=rendered.subject, message_id=message_id)
        return SendResult(status="sent", message_id=message_id)

    def _post(self, payload: dict, idempotency_key: str | None) -> str:
        headers = {
            "Authorization": f"Bearer {self.settings.resend_api_key}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        @retry(
            reraise=True,
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception_type((RateLimitedError, httpx.TransportError, ProviderError)),
        )
        def _do() -> str:
            with httpx.Client(timeout=45) as client:
                resp = client.post(RESEND_URL, headers=headers, json=payload)
            if resp.status_code == 429:
                raise RateLimitedError("Resend rate limit")
            if resp.status_code >= 500:
                raise ProviderError(f"Resend {resp.status_code}")
            if resp.status_code >= 400:
                raise ProviderError(f"Resend {resp.status_code}: {resp.text[:300]}")
            return str(resp.json().get("id", ""))

        return _do()

    def _write_to_outbox(
        self, to: list[str], rendered: RenderedEmail, payload: dict, attachments: list[Path] | None
    ) -> SendResult:
        self.outbox.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        safe_to = to[0].replace("@", "_at_").replace("/", "_")
        base = self.outbox / f"{stamp}_{safe_to}"
        html_path = base.with_suffix(".html")
        html_path.write_text(rendered.html, encoding="utf-8")
        meta = {
            "to": to,
            "from": payload["from"],
            "subject": rendered.subject,
            "attachments": [p.name for p in (attachments or []) if p.exists()],
            "reason": "RESEND_API_KEY is not set — email rendered to disk instead of sent",
        }
        base.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        log.info("email.rendered_not_sent", to=to, path=str(html_path))
        return SendResult(status="rendered_not_sent", path=str(html_path))
