from __future__ import annotations

import math
from dataclasses import dataclass


class BudgetExceeded(Exception):
    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


@dataclass
class JobLLMBudget:
    """Shared across all loops, extraction and explanations in one job (MAX_LLM_CALLS)."""

    left: int
    used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def can(self, n: int = 1) -> bool:
        return self.left >= n

    def consume(self, n: int = 1) -> None:
        if self.left < n:
            raise BudgetExceeded("llm_calls")
        self.left -= n
        self.used += n

    def add_usage(self, usage: dict) -> None:
        self.input_tokens += int(usage.get("input_tokens", 0) or 0)
        self.output_tokens += int(usage.get("output_tokens", 0) or 0)


@dataclass
class Budget:
    turns_left: int
    searches_left: int
    fetches_left: int
    llm: JobLLMBudget

    def consume_turn(self) -> None:
        if self.turns_left <= 0:
            raise BudgetExceeded("turns")
        self.turns_left -= 1

    def consume_search(self) -> None:
        if self.searches_left <= 0:
            raise BudgetExceeded("searches")
        self.searches_left -= 1

    def consume_fetch(self) -> None:
        if self.fetches_left <= 0:
            raise BudgetExceeded("fetches")
        self.fetches_left -= 1

    def split(self, n: int) -> list[Budget]:
        if n <= 1:
            return [self]
        return [Budget(self.turns_left, math.ceil(self.searches_left / n), math.ceil(self.fetches_left / n), self.llm)
                for _ in range(n)]
