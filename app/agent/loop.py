"""ResearchLoop — bounded LLM tool-calling loop using the provider's NATIVE tool-use API.

Each turn: the model sees the current evidence state + strict tool schemas and picks the next action(s);
the orchestrator validates/executes them, extraction + validation update the state, and the updated state is
returned. The loop stops on finish_research, budget/deadline, or provider failure. History is append-only.
"""

from __future__ import annotations

import asyncio
import json
import time

from app.agent.state import LoopSession
from app.agent.tool_executor import ToolExecutor
from app.agent.tool_specs import specs_for
from app.core.errors import ProviderError
from app.models.common import ErrorCategory
from app.orchestrator.budgets import BudgetExceeded
from app.orchestrator.event_bus import JobEventLog
from app.providers.llm.base import LLMProvider

MAX_PARALLEL_TOOLS = 3


def render_state_text(session: LoopSession) -> str:
    state = session.render(session)
    state["budget"] = {"turns_left": session.budget.turns_left, "searches_left": session.budget.searches_left,
                       "fetches_left": session.budget.fetches_left, "seconds_left": session.seconds_left}
    state["searched_queries"] = sorted(session.searched_queries)[-12:]
    state["recent_failures"] = session.recent_failures[-5:]
    return json.dumps(state, separators=(",", ":"), default=str)


class ResearchLoop:
    def __init__(self, session: LoopSession, llm: LLMProvider, executor: ToolExecutor, events: JobEventLog,
                 effort: str):
        self.s = session
        self.llm = llm
        self.ex = executor
        self.events = events
        self.effort = effort
        self.messages: list[dict] = []

    async def run(self) -> LoopSession:
        s = self.s
        tools = specs_for(s.mode.tools)
        self.messages = [{"role": "user", "content": "Research request (JSON state):\n" + render_state_text(s)}]
        max_tokens_retries = 0
        try:
            timeout = max(1.0, s.deadline_at - time.monotonic())
            async with asyncio.timeout(timeout):
                while True:
                    if s.budget.turns_left <= 0:
                        s.stop_reason = "budget:turns"
                        break
                    if not s.budget.llm.can(1):
                        s.stop_reason = "budget:llm_calls"
                        break
                    s.budget.consume_turn()
                    s.budget.llm.consume(1)
                    s.turns += 1
                    turn = await self.llm.research_turn(system=s.mode.system_prompt, tools=tools,
                                                        messages=self.messages, effort=self.effort)
                    s.budget.llm.add_usage(turn.usage)
                    if turn.stop_reason == "refusal":
                        s.stop_reason = "provider_refusal"
                        self.events.append("warning", s.mode.mode.value, "The model declined this step; stopping safely",
                                           error_category=ErrorCategory.refusal)
                        break
                    self.messages.append({"role": "assistant", "content": turn.raw_content})  # append-only
                    if turn.stop_reason == "pause_turn":
                        continue
                    if turn.stop_reason == "max_tokens" and not turn.tool_calls:
                        if max_tokens_retries >= 1:
                            s.stop_reason = "model_ended:max_tokens"
                            break
                        max_tokens_retries += 1
                        self.messages.append({"role": "user", "content": "Continue with a tool call."})
                        continue
                    if not turn.tool_calls:
                        s.stop_reason = "model_ended"
                        break
                    names = ", ".join(c.name for c in turn.tool_calls)
                    self.events.append("agent_turn", s.mode.mode.value,
                                       f"Agent turn {s.turns}: chose {names}", tool=turn.tool_calls[0].name,
                                       duration_ms=turn.duration_ms, payload={"loop": s.name, "tools": [
                                           {"name": c.name, "purpose": c.input.get("purpose"), "gap": c.input.get("gap"),
                                            "target_program_id": c.input.get("target_program_id")}
                                           for c in turn.tool_calls]})
                    sem = asyncio.Semaphore(MAX_PARALLEL_TOOLS)

                    async def run_one(call, sem=sem):
                        async with sem:
                            return await self.ex.execute(call)

                    results = await asyncio.gather(*(run_one(c) for c in turn.tool_calls))
                    blocks: list[dict] = [{"type": "tool_result", "tool_use_id": c.id, "content": r.content,
                                           "is_error": r.is_error} for c, r in zip(turn.tool_calls, results, strict=True)]
                    blocks.append({"type": "text", "text": "Current research state:\n" + render_state_text(s)})
                    self.messages.append({"role": "user", "content": blocks})  # ONE user message for all results
                    if self.ex.provider_auth_failed:
                        s.stop_reason = "provider_error:provider_auth"
                        break
                    if s.stop_reason:
                        break
        except TimeoutError:
            s.stop_reason = "deadline"
        except BudgetExceeded as e:
            s.stop_reason = f"budget:{e.what}"
        except ProviderError as e:
            s.stop_reason = f"provider_error:{e.category.value}"
            self.events.append("warning", s.mode.mode.value, f"AI provider error ({e.category.value}); stopping loop",
                               error_category=e.category)
        return s
