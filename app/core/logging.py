"""JSON logging with secret redaction. Never log profiles, page text, drafts or prompts."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

_KEY_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"gsk_[A-Za-z0-9_\-]+"),
    re.compile(r"AQ\.[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{30,}"),
    re.compile(r"tvly-[A-Za-z0-9_\-]+"),
    re.compile(r"re_[A-Za-z0-9_]{16,}"),
]
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {"message", "asctime"}
_LOG_CONTEXT: ContextVar[dict | None] = ContextVar("gradbeacon_log_context", default=None)


@contextmanager
def bind_log_context(**fields):
    """Attach correlation fields to logs emitted within this async task's context."""
    token = _LOG_CONTEXT.set({**(_LOG_CONTEXT.get() or {}), **fields})
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, "[REDACTED]")
    for p in _KEY_PATTERNS:
        text = p.sub("[REDACTED]", text)
    return text


def _redact_value(value, secrets: list[str]):
    """Redact strings recursively so structured log fields receive the same protection as messages."""
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, dict):
        return {key: _redact_value(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, secrets) for item in value]
    return value


class SecretRedactionFilter(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in (_LOG_CONTEXT.get() or {}).items():
            if not hasattr(record, key):
                setattr(record, key, value)
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = str(record.msg)
        record.msg = redact(msg, self.secrets)
        record.args = None
        for k, v in list(vars(record).items()):
            if k not in _RESERVED:
                setattr(record, k, _redact_value(v, self.secrets))
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in vars(record).items():
            if k not in _RESERVED and not k.startswith("_"):
                try:
                    json.dumps(v)
                    out[k] = v
                except TypeError:
                    out[k] = str(v)
        if record.exc_info and record.levelno >= logging.ERROR:
            out["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(out)


def configure_logging(level: str, secrets: list[str]) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SecretRedactionFilter(secrets))
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "httpx2", "groq", "trafilatura", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
