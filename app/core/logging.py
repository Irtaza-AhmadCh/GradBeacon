"""JSON logging with secret redaction. Never log profiles, page text, drafts or prompts."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

_KEY_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"gsk_[A-Za-z0-9_\-]+"),
    re.compile(r"tvly-[A-Za-z0-9_\-]+"),
    re.compile(r"re_[A-Za-z0-9_]{16,}"),
]
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {"message", "asctime"}


def redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, "[REDACTED]")
    for p in _KEY_PATTERNS:
        text = p.sub("[REDACTED]", text)
    return text


class SecretRedactionFilter(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            msg = str(record.msg)
        record.msg = redact(msg, self.secrets)
        record.args = None
        for k, v in list(vars(record).items()):
            if k not in _RESERVED and isinstance(v, str):
                setattr(record, k, redact(v, self.secrets))
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
