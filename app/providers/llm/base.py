from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


@dataclass
class LLMToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMTurn:
    raw_content: list[Any]  # appended to history unchanged (append-only)
    tool_calls: list[LLMToolCall]
    text: str
    stop_reason: str
    usage: dict = field(default_factory=dict)
    duration_ms: int = 0


class LLMProvider(Protocol):
    async def research_turn(self, *, system: str, tools: list[dict], messages: list[dict], effort: str) -> LLMTurn: ...

    async def extract_json(self, *, system: str, user_content: str, schema_model: type[M], effort: str) -> M: ...

    async def write_json(self, *, system: str, user_content: str, schema_model: type[M], effort: str) -> M: ...
