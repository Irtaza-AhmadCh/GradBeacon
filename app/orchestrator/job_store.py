"""In-memory job store (no database by design — PRD FR-44). One worker process only."""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.core import clock
from app.core.errors import Expired, NotFound, TooBusy
from app.core.ids import new_id
from app.models.common import SourceMode
from app.models.outreach import Draft
from app.models.professor import ProfessorMatch
from app.models.profile import StudentProfile
from app.models.program import ExcludedProgram, ProgramOpportunity
from app.models.results import Checklist, DecisionRecord, Explanation, MatchResult, PendingDecision
from app.orchestrator.event_bus import JobEventLog

_ID_RE = re.compile(r"^(job|vjob|pjob)_[0-9a-f]{16}$")


class JobKind(StrEnum):
    programs = "programs"
    verify = "verify"
    professors = "professors"


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    partial = "partial"
    fallback = "fallback"
    failed = "failed"


FINISHED = {JobStatus.completed, JobStatus.partial, JobStatus.fallback, JobStatus.failed}


@dataclass
class ShortlistState:
    approved: bool = False
    program_ids: list[str] = field(default_factory=list)
    approved_at: datetime | None = None


@dataclass
class Job:
    job_id: str
    kind: JobKind
    parent_job_id: str | None
    status: JobStatus
    created_at: datetime
    events: JobEventLog
    source_mode: SourceMode = SourceMode.live
    finished_at: datetime | None = None
    stop_reason: str | None = None
    profile: StudentProfile | None = None
    profile_summary: dict = field(default_factory=dict)
    qualification: str | None = None
    programs: dict[str, ProgramOpportunity] = field(default_factory=dict)
    emitted: list[str] = field(default_factory=list)  # program ids emitted as results (validated)
    matches: dict[str, MatchResult] = field(default_factory=dict)
    checklists: dict[str, Checklist] = field(default_factory=dict)
    explanations: dict[str, Explanation] = field(default_factory=dict)
    pending: dict[str, list[PendingDecision]] = field(default_factory=dict)
    suggested_sources: dict[str, list[str]] = field(default_factory=dict)
    gap_attempts: Counter = field(default_factory=Counter)
    excluded: list[ExcludedProgram] = field(default_factory=list)
    fallback_results: list[str] = field(default_factory=list)
    professors: dict[str, ProfessorMatch] = field(default_factory=dict)
    professor_order: list[str] = field(default_factory=list)
    decisions: dict[str, DecisionRecord] = field(default_factory=dict)
    shortlist: ShortlistState = field(default_factory=ShortlistState)
    drafts: dict[str, Draft] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    seen_official_urls: set[str] = field(default_factory=set)
    research_finished: bool = False
    verify_running: set[str] = field(default_factory=set)
    started_monotonic: float = field(default_factory=time.monotonic)
    first_result_ms: int | None = None
    llm_budget: Any = None  # JobLLMBudget
    research: Any = None  # SharedResearch (pages/links fetched in this job; never the profile)
    replay_captured_at: str | None = None
    task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    expires_at: float = 0.0

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)


class JobStore:
    def __init__(self, ttl_seconds: int, max_jobs: int, max_concurrent: int):
        self.ttl = ttl_seconds
        self.max_jobs = max_jobs
        self.max_concurrent = max_concurrent
        self.jobs: dict[str, Job] = {}
        self.tombstones: dict[str, float] = {}
        self.drafts_index: dict[str, str] = {}  # draft_id -> job_id

    def running_count(self) -> int:
        return sum(1 for j in self.jobs.values() if j.status in (JobStatus.queued, JobStatus.running)
                   and j.kind != JobKind.verify)

    def create(self, kind: JobKind, parent_job_id: str | None = None, profile: StudentProfile | None = None,
               count_toward_concurrency: bool = True) -> Job:
        self.sweep()
        if count_toward_concurrency and self.running_count() >= self.max_concurrent:
            raise TooBusy()
        if len(self.jobs) >= self.max_jobs:
            finished = sorted((j for j in self.jobs.values() if j.status in FINISHED), key=lambda j: j.created_at)
            for j in finished[: len(self.jobs) - self.max_jobs + 1]:
                self._evict(j.job_id)
            if len(self.jobs) >= self.max_jobs:
                raise TooBusy()
        prefix = {JobKind.programs: "job", JobKind.verify: "vjob", JobKind.professors: "pjob"}[kind]
        jid = new_id(prefix)
        job = Job(job_id=jid, kind=kind, parent_job_id=parent_job_id, status=JobStatus.queued,
                  created_at=clock.utc_now(), events=JobEventLog(jid, kind.value), profile=profile,
                  expires_at=time.monotonic() + self.ttl)
        self.jobs[jid] = job
        return job

    def get(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is not None:
            if time.monotonic() > job.expires_at and job.status in FINISHED:
                self._evict(job_id)
                raise Expired()
            return job
        if job_id in self.tombstones or _ID_RE.match(job_id or ""):
            # Well-formed id we don't have: expired or lost on restart (in-memory by design).
            raise Expired()
        raise NotFound("Unknown job id")

    def _evict(self, job_id: str) -> None:
        job = self.jobs.pop(job_id, None)
        if job:
            for d in job.drafts:
                self.drafts_index.pop(d, None)
        self.tombstones[job_id] = time.monotonic() + 2 * self.ttl

    def sweep(self) -> None:
        now = time.monotonic()
        for jid, job in list(self.jobs.items()):
            if now > job.expires_at and job.status in FINISHED:
                self._evict(jid)
        for jid, exp in list(self.tombstones.items()):
            if now > exp:
                self.tombstones.pop(jid, None)

    def find_draft(self, draft_id: str) -> tuple[Job, Draft]:
        jid = self.drafts_index.get(draft_id)
        if not jid:
            if re.match(r"^drf_[0-9a-f]{16}$", draft_id or ""):
                raise Expired("This draft has expired or the server restarted")
            raise NotFound("Unknown draft id")
        job = self.get(jid)
        draft = job.drafts.get(draft_id)
        if draft is None:
            raise NotFound("Unknown draft id")
        return job, draft
