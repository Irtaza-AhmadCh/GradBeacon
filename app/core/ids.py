from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def stable_program_id(institution: str, program_name: str, level: str) -> str:
    raw = f"{_norm(institution)}|{_norm(program_name)}|{level}"
    return "prg_" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def stable_professor_id(institution: str, name: str) -> str:
    raw = f"{_norm(institution)}|{_norm(name)}"
    return "prof_" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()
