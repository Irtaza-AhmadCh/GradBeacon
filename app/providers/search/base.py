from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    domain: str
    score: float | None = None
    provider: str


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, limit: int, domain_hints: list[str] | None) -> list[SearchResult]: ...
