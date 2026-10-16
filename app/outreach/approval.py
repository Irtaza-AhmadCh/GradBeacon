"""ApprovalService — HMAC, single-use approval tokens bound to the exact draft text and official recipient."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import timedelta

from app.core import clock
from app.core.errors import ConfigError, InvalidState, ValidationFailed
from app.core.ids import new_id, sha256_hex
from app.models.outreach import Approval, Draft


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def content_hash(subject: str, body: str) -> str:
    return sha256_hex(subject + "\n\n" + body)


class ApprovalService:
    def __init__(self, secret: str | None, ttl_seconds: int):
        self.secret = secret.encode() if secret else None
        self.ttl = ttl_seconds

    def _require(self) -> bytes:
        if not self.secret or len(self.secret) < 32:
            raise ConfigError("Approval signing is not configured (APPROVAL_TOKEN_SECRET)")
        return self.secret

    def issue(self, draft: Draft) -> tuple[str, Approval]:
        secret = self._require()
        now = clock.utc_now()
        appr = Approval(approval_id=new_id("apv"), content_hash=draft.content_hash,
                        recipient_hash=sha256_hex(draft.recipient_email.lower()), issued_at=now,
                        expires_at=now + timedelta(seconds=self.ttl), consumed=False)
        payload = {"approval_id": appr.approval_id, "draft_id": draft.draft_id, "content_hash": appr.content_hash,
                   "recipient_hash": appr.recipient_hash, "exp": int(appr.expires_at.timestamp())}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        sig = hmac.new(secret, raw, hashlib.sha256).digest()
        return f"{_b64(raw)}.{_b64(sig)}", appr

    def verify_and_consume(self, draft: Draft, token: str) -> Approval:
        secret = self._require()
        try:
            p64, s64 = token.split(".", 1)
            raw, sig = _unb64(p64), _unb64(s64)
        except Exception as e:  # noqa: BLE001
            raise ValidationFailed("Malformed approval token", code="bad_token") from e
        if not hmac.compare_digest(hmac.new(secret, raw, hashlib.sha256).digest(), sig):
            raise ValidationFailed("Invalid approval token", code="bad_token")
        payload = json.loads(raw)
        if payload.get("exp", 0) < int(clock.utc_now().timestamp()):
            raise InvalidState("Approval expired; approve the draft again", code="approval_expired")
        appr = draft.approval
        if appr is None or appr.approval_id != payload.get("approval_id"):
            raise InvalidState("Approval does not match this draft's current approval", code="approval_consumed")
        if appr.consumed:
            raise InvalidState("This approval was already used; approve again to retry", code="approval_consumed")
        if payload.get("draft_id") != draft.draft_id:
            raise InvalidState("Approval is for a different draft", code="bad_token")
        if payload.get("content_hash") != draft.content_hash or appr.content_hash != draft.content_hash:
            raise InvalidState("The draft changed after approval; review and approve again", code="draft_changed")
        if payload.get("recipient_hash") != sha256_hex(draft.recipient_email.lower()):
            raise InvalidState("Recipient changed after approval", code="recipient_mismatch")
        appr.consumed = True  # consumed BEFORE any network call: one attempt per approval
        return appr
