"""VerifyFieldWorkflow — student-requested 'Verify this' for one field of one program (human-in-the-loop)."""

from __future__ import annotations

import asyncio
import logging
import time

from app.agent import prompts
from app.agent.loop import ResearchLoop
from app.agent.state import LoopSession, ModeConfig, ResearchMode, SharedResearch, useful_links
from app.agent.tool_executor import ToolExecutor
from app.agent.tool_specs import RESEARCH_TOOL_NAMES
from app.core import clock
from app.models.common import FieldStatus
from app.models.profile import StudentProfile
from app.orchestrator.budgets import Budget, JobLLMBudget
from app.orchestrator.context import AppContext
from app.orchestrator.job_store import Job, JobStatus
from app.orchestrator.program_workflow import ProgramPageHandler
from app.orchestrator.results import emit_program
from app.tools.url_safety import canonicalize

log = logging.getLogger("gradbeacon.verify")
VERIFY_MODE = ModeConfig(ResearchMode.verify, RESEARCH_TOOL_NAMES, prompts.VERIFY)
FIELD_STATUS_ATTR = {"tuition": "tuition_status", "funding": "funding_status", "deadline": "deadline_status",
                     "documents": "documents_status", "min_gpa": "requirements_status",
                     "english_test": "requirements_status", "direct_entry_phd": "requirements_status"}


def _field_status(p, field: str) -> str:
    return getattr(p, FIELD_STATUS_ATTR[field]).value


async def run_verify_workflow(ctx: AppContext, vjob: Job, parent: Job, program_id: str, field: str,
                              source_url: str | None, profile: StudentProfile | None) -> None:
    s = ctx.settings
    vjob.status = JobStatus.running
    p = parent.programs[program_id]
    vjob.events.append("job_started", "start", f"Verifying {field.replace('_', ' ')} for {p.program_name}")
    before = _field_status(p, field)
    key = f"{program_id}:{field}"
    try:
        shared: SharedResearch = parent.research or SharedResearch()
        parent.research = shared
        for src in p.sources:
            shared.seeded_urls.add(canonicalize(src.final_url))
        if source_url:
            shared.seeded_urls.add(canonicalize(source_url))
        for u in parent.suggested_sources.get(program_id, []):
            shared.seeded_urls.add(u)
        deadline_at = time.monotonic() + s.verify_deadline_seconds
        llm_budget = JobLLMBudget(left=10)
        parent.llm_budget = llm_budget
        vjob.llm_budget = llm_budget
        if profile is not None:
            parent.profile = profile  # temporary, for re-scoring only; cleared below
        handler = ProgramPageHandler(ctx, parent, shared, {"verify": p.degree_level}, {p.degree_level: 99}, [],
                                     asyncio.Semaphore(1), deadline_at, restrict_to=program_id,
                                     event_log=vjob.events)

        def render(session: LoopSession) -> dict:
            cur = parent.programs[program_id]
            return {"mode": "verify", "target_program_id": program_id, "program": cur.program_name,
                    "institution": cur.institution_name, "program_url": cur.official_program_url,
                    "field": field, "field_status": _field_status(cur, field),
                    "suggested_sources": sorted({source_url} - {None}) + parent.suggested_sources.get(program_id, [])[:3],
                    "useful_links": useful_links(shared, limit=8)}

        sess = LoopSession(name="verify", mode=VERIFY_MODE, budget=Budget(4, 2, 3, llm_budget),
                           deadline_at=deadline_at, shared=shared, on_page=handler,
                           on_fetch_failed=handler.on_fetch_failed, render=render,
                           known_program_ids=lambda: {program_id})
        ex = ToolExecutor(sess, ctx.providers.search, ctx.providers.fetcher, vjob.events, parent.gap_attempts,
                          vjob.counts, s.extra_official)
        await ResearchLoop(sess, ctx.providers.llm, ex, vjob.events, s.llm_effort_research).run()
        after = _field_status(parent.programs[program_id], field)
        vjob.stop_reason = sess.stop_reason
        if after == FieldStatus.verified.value and before != after:
            msg = f"{field.replace('_', ' ').title()} is now verified from an official source"
        elif after == FieldStatus.conflicting.value:
            msg = f"Sources still conflict on {field.replace('_', ' ')}; please review the competing claims"
        else:
            msg = f"Could not verify {field.replace('_', ' ')}; it remains marked as {after.replace('_', ' ')}"
        vjob.warn(msg)
        emit_program(ctx, parent, program_id, shared)  # program_updated on the PARENT stream
        vjob.status = JobStatus.completed
        vjob.events.append("program_updated", "results", msg,
                           payload={"program_id": program_id, "field": field, "before": before, "after": after})
    except Exception:  # noqa: BLE001
        log.exception("verify_failed", extra={"job_id": vjob.job_id})
        vjob.status = JobStatus.failed
        vjob.warn("Verification stopped due to an internal error; the field remains unverified.")
    finally:
        parent.profile = None
        parent.verify_running.discard(key)
        vjob.finished_at = clock.utc_now()
        vjob.events.append("done", "done", vjob.warnings[-1] if vjob.warnings else "Verification finished",
                           counts=dict(vjob.counts), payload={"status": vjob.status.value,
                                                              "stop_reason": vjob.stop_reason})
