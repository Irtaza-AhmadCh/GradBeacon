# GradBeacon — backend & agentic research system

Evidence-first US graduate-program research agent with human approval gates.
**Human + AI:** the agent researches, extracts, verifies and drafts; the student reviews evidence, resolves
uncertainty, approves the shortlist, edits the outreach email, and explicitly approves it. Nothing is submitted
or sent automatically. Fit scores are **profile-fit scores, not admission probabilities**.

- Product source of truth: `GradBeacon_Product_Requirements_Document.md` (PRD)
- Agent contract: `agent (1).md` · Build plan: `TECHNICAL_IMPLEMENTATION_PLAN.md`
- Interim UI: Swagger at `/docs`, plus `scripts/run_research_cli.py`. The web frontend is the next phase (`frontend/README.md`).

---

## 1. What makes it an agent (hackathon definition: an LLM that uses tools, decides what to do next, and completes a workflow)

```
Student profile ──► Orchestrator (deterministic: budgets, permissions, validation, scoring)
                         │
                         ▼
        ┌──────── Research loop (Groq LLM, NATIVE tool calling, tool_choice=auto)  ────────┐
        │  sees: evidence state per program (verified / not_found / conflicting / unavailable), │
        │        open_gaps, useful official links, remaining budget                              │
        │  chooses: search_web │ fetch_page │ retrieve_evidence │ finish_research               │
        └──────────────┬───────────────────────────────────────────────────────────────────────┘
                       ▼ every call validated by ToolExecutor (allowlist, args, budgets, URL provenance, .edu policy)
             fetch ─► extraction (structured output) ─► EvidenceValidator (verbatim quote must exist in page)
                    ─► RecordMerger (conflicts preserved) ─► gaps recomputed ─► state returned to the model
                       ▼
        validated program emitted progressively (SSE) ─► deterministic score + checklist + "Needs your decision"
                       ▼
   HUMAN: resolve/verify uncertainty ─► approve shortlist ─► (professor research, same loop) ─► draft email
          ─► edit ─► explicit final approval (hash-bound token) ─► send (deferred) or copy/mailto
```

The loop is adaptive: each program has different missing evidence, so the model picks a targeted next action
(e.g. fetch the "Tuition & Fees" link for program A, search the funding page for program B, stop when enough is
verified). Deterministic code owns validation, scoring, permissions and limits. The model never has send/submit/
shell tools; untrusted page text is fenced and cannot change permissions (prompt-injection pages are flagged).

| Rubric item | Where |
|---|---|
| Native LLM tool calling | `app/agent/loop.py`, `app/agent/tool_specs.py` (strict schemas), `app/providers/llm/anthropic_provider.py` |
| Evidence-first, never fabricates | `app/validation/evidence_validator.py` (quote-in-source + value-in-quote), `merger.py`, field-level statuses |
| Human-in-the-loop gates | shortlist approval, "Needs your decision / Verify this", draft edit → explicit approval token → single send |
| Uncertainty handling | `conflicting / not_found / unavailable / curated_fallback` per field; pending decisions; stale-deadline warnings |
| Agent trace | real backend events over SSE `/api/research/{id}/events` (code-generated summaries, no chain-of-thought) |
| Demo mode + cached fallback | `APP_MODE=fallback_only` (curated, labelled) · `POST /api/demo/replay` / `APP_MODE=demo_replay` (recorded real run) |
| Progressive results | `program` events emitted as soon as each record validates; explanations arrive later as `program_updated` |
| Useful deliverable | ranked shortlist with evidence, combined checklist, grounded editable outreach draft |

---

## 2. Quick start (local)

```bash
py -3.12 -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt        # macOS/Linux: .venv/bin/pip
cp .env.example .env                                      # then fill in keys (see §3)
.venv/Scripts/python scripts/verify_providers.py          # real Tavily + Groq tool-use + fetch checks
.venv/Scripts/uvicorn app.main:app --port 8000 --workers 1
# open http://localhost:8000/docs   or:
.venv/Scripts/python scripts/run_research_cli.py --base-url http://localhost:8000
.venv/Scripts/python -m pytest -q                          # 49 tests, no network/keys needed
```

Without keys the app still works in **curated fallback** mode (`/health` shows `effective_mode: fallback_only`).

---

## 3. Environment variables

All secrets are server-side only (never in responses, logs or frontend). `.env` is git-ignored; `.env.example`
is the committed template. At startup, missing keys are reported **by name only** and live mode is downgraded to
fallback instead of failing.

| Variable | Required | Where to get it / value |
|---|---|---|
| `GROQ_API_KEY_1` … `GROQ_API_KEY_6` | **≥1 required (live)** | console.groq.com → API Keys → Create API Key (`gsk_…`). Free tier = **8,000 tokens/min and 1,000 requests/day per organization**; a full research job needs ~30–40k tokens. Keys are rotated with automatic failover on 429/5xx. Extra keys only add capacity if they come from **different Groq organizations/accounts**; otherwise upgrade the org to the Developer tier. |
| `TAVILY_API_KEY` | **Yes (live)** | app.tavily.com → sign up → API Keys (`tvly-…`). Free tier ≈1,000 credits/month; each search/extract uses credits. |
| `APPROVAL_TOKEN_SECRET` | **Yes (outreach)** | Any random ≥32 chars: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Use a **different** value per deployment. |
| `APP_ENV` | No | `production` on the host. |
| `APP_MODE` | No | `live` (default) · `fallback_only` (no provider calls) · `demo_replay` (serve recorded run). |
| `ALLOWED_ORIGINS` | When frontend is on another origin | CSV, e.g. `https://gradbeacon-web.example.app`. |
| `TRUST_PROXY_HEADERS` | No | `true` only if the platform proxy sets `X-Forwarded-For` (for per-IP rate limits). |
| `LLM_MODEL` | No | Default `openai/gpt-oss-120b` (native tool calling + strict JSON; `reasoning_effort` from `LLM_EFFORT_*`). `openai/gpt-oss-20b` is faster/cheaper. |
| `EMAIL_SEND_MODE` | No | `live` (default, per owner). Sending provider **not implemented yet** — see §6. |
| Budgets (`MAX_LOOP_TURNS`, `MAX_SEARCH_CALLS`, `MAX_PAGE_FETCHES`, `MAX_LLM_CALLS`, `REQUEST_DEADLINE_SECONDS`, …) | No | Initial assumptions; tune after measuring (see `.env.example`). |
| `FETCH_PROVIDER_FALLBACK` | No | `true`: when a .edu site refuses direct fetch (bot wall/403), retrieve it via Tavily Extract; labelled `retrieval_method=provider_extract`. |

---

## 4. Deployment (Cloud of Alternate Futures + backup)

The app is a single Python 3.12 web service, **one worker** (in-memory job state by design: no DB, no accounts).

1. Push this repo to GitHub (`.env` is ignored — verify with `git status`).
2. Platform → **Add Service** from the GitHub repo → Python.
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers` (also in `Procfile`)
   - Python version: `.python-version` / `runtime.txt` = 3.12
3. Set env vars in the platform's secret manager: `GROQ_API_KEY_1` (+ more), `TAVILY_API_KEY`, `APPROVAL_TOKEN_SECRET`,
   `APP_ENV=production` (+ `ALLOWED_ORIGINS` once the frontend exists).
4. Check `https://<primary>/health` → `effective_mode: "live"`, providers `configured`, `curated_records: 12`.
5. **Backup instance:** create a second service from the same repo/commit with **separately entered** secrets
   (its own `APPROVAL_TOKEN_SECRET`). Optionally set `APP_MODE=demo_replay` on the backup once a replay is recorded.
6. Run `python scripts/smoke_test.py --base-url https://<primary> --base-url https://<backup> --live`.

[VERIFY on the platform] how `$PORT` is provided, request timeouts and whether SSE passes through its proxy. If SSE
is buffered, clients use the polling fallback `GET /api/research/{id}?since_seq=N` (fully supported).
If the platform cannot keep secrets server-side, deploy with `APP_MODE=fallback_only` rather than exposing keys.

---

## 5. API (full schemas at `/docs` and `/openapi.json`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness + configuration status (names/states only, never values) |
| POST | `/api/research/programs` | Start research (202 + `events_url`, `snapshot_url`); 200 curated snapshot if live unavailable |
| GET | `/api/research/{job_id}/events` | SSE trace + progressive results (`Last-Event-ID` resume) |
| GET | `/api/research/{job_id}?since_seq=` | Snapshot / polling fallback (validated records only) |
| POST | `/api/research/{job_id}/programs/{pid}/verify` | "Verify this": agent checks one uncertain field (verify-mode loop) |
| POST | `/api/research/{job_id}/programs/{pid}/decisions` | Student continues with an uncertainty (stays flagged) |
| POST | `/api/research/{job_id}/shortlist` | **Human approval** of shortlist → per-program + combined checklist |
| POST | `/api/research/professors` | Professor research (requires approved shortlist) |
| POST | `/api/outreach/draft` | Grounded editable draft (official email required; never sends) |
| GET/PUT | `/api/outreach/drafts/{id}` | Read / edit (version-checked; edits invalidate approval) |
| POST | `/api/outreach/drafts/{id}/approve` | Explicit approval of exact text (`confirm: true` + `"I approve sending this email"`) |
| POST | `/api/outreach/send` | Single send with approval token (returns 503 until a sender is implemented) |
| POST | `/api/outreach/drafts/{id}/cancel` | Cancel draft |
| POST | `/api/demo/replay` | Replay of a recorded real run, labelled with capture time |

Errors use `{"error": {"code", "message", "request_id"}}`; 400/404/409/410/413/429/503 as documented in the plan.

---

## 6. Outreach email sending — deferred by owner decision

Drafting, editing, explicit approval (HMAC token bound to the draft hash + official recipient, single use, 10-min
expiry), recipient lock, and no-retry semantics are implemented and tested. **Actual delivery is a stub**:
`app/providers/email/base.py::UnconfiguredEmailProvider`. Until a sender is added, approval returns a
`manual_send` block (mailto link + copy text) so the student sends the exact approved text from their own account
(PRD FR-42's optional alternative). To add sending later: implement `EmailProvider.send` and return it from
`app/providers/factory.py::build_email_provider` — the gate in `app/outreach/sender.py` needs no changes.

---

## 7. Curated fallback dataset (P0)

`data/curated_programs.json` — 12 records (CS, Data Science, ECE; 6 Master's / 6 PhD) at Stanford, Cornell, CMU,
Rice, Rochester, Miami, UVA, UNC Charlotte, Illinois, Purdue, Virginia Tech. Built by
`scripts/curate_fallback.py` from `data/seed_parts/*.json`: every claim is a **verbatim quote re-verified against
a fresh fetch** of the official page through the same validator/merger as live research (0 rejections at build).
Records are always labelled `Curated fallback (last verified YYYY-MM-DD) — not freshly verified`.
Rebuild/refresh: `python scripts/curate_fallback.py --seed data/seed_parts/*.json --out data/curated_programs.json`.

Notable real-world findings during curation (shown honestly by the product, not hidden):
- Several official pages are stale (UNC Charlotte lists a Fall 2026 deadline that has passed → flagged warning).
- Official pages contradict each other (Rice: 2 vs 3 recommendation letters; VT: GRE optional vs required for some
  international applicants; UVA: "will receive" vs "typically receive" funding).
- Many .edu sites block automated fetches (403 bot walls: Michigan, Cornell ECE, NYU, UT Austin…). Live research
  therefore uses Tavily Extract as a labelled fallback (`FETCH_PROVIDER_FALLBACK`).

---

## 8. Scoring v1 (deterministic, versioned — `app/scoring/weights.py`)

Weights: academic 0.35 · research 0.30 · funding 0.20 · preference 0.15 — initial values, **not empirically
validated**. Unknown dimensions are excluded (never scored as 0); overall is `null` if fewer than 2 dimensions are
known. GPA is compared only on the same scale; funding existence never implies eligibility; explicit "no funding"
is a known negative (`not_offered`). Ranking: score → verified-field count → name, separately per degree level.

---

## 9. Six-minute demo flow

1. **0:00 Problem** — international applicants reconcile scattered official pages for hours.
2. **0:30 Live agent run** — submit `data/demo_profile.json`; show the SSE trace: `agent_turn`, `gap_selected`
   ("Evidence gap 'tuition' selected…"), `page_fetched`, `facts_extracted` (validated vs rejected claims).
3. **2:30 Evidence** — open a result: field labels, source URL + verbatim quote per fact, one honest unknown.
4. **3:30 Human + AI** — a "Needs your decision / Verify this" item: click *Verify this* (agent re-researches one
   field) or *Continue with uncertainty*; then approve the shortlist (gate enforced by the API).
5. **4:30 Deliverable** — combined checklist; professor research → grounded draft → edit → explicit approval →
   mailto/copy handoff (automatic send deferred).
6. **5:00 Resilience** — show `/health`, a `fallback_only` instance with labelled curated records, or
   `POST /api/demo/replay` ("Cached demo replay captured …").
7. **5:30 Metrics** — from `counts`: programs, official sources, verified vs rejected claims, `first_result_ms`.

## 10. Pre-demo smoke-test checklist (agent spec §14.5)

- [ ] `scripts/verify_providers.py` all PASS on the deploy keys (Tavily, Groq tool use, structured output, fetch).
- [ ] `scripts/smoke_test.py --live` against **primary and backup**: health, secrets absent, results, gap follow-up.
- [ ] Record 3–4 named programs from a live run: official URL + fields actually verified (paste below).
- [ ] First validated result ≤ 45–60 s (`counts.first_result_ms`), else tune `MAX_*` budgets / `LLM_MODEL`.
- [ ] Invalid `TAVILY_API_KEY` locally → curated fallback, `fallback_used` event, key absent from logs.
- [ ] Curated fallback labels visible; replay labels + capture time visible (`scripts/record_demo.py` first).
- [ ] A conflicting/unknown field shows "Needs your decision / Verify this".
- [ ] Shortlist approval gates checklist/professors; draft edit invalidates approval; approval needs exact text.
- [ ] No keys or student data in frontend bundle, API responses, or logs.

Live-run verification record: _(fill in after the first live run)_

## 11. Known limitations

- In-memory jobs: restart loses jobs (API returns 410). Run exactly one worker.
- DNS-rebinding residual risk between SSRF check and connect (mitigated by re-validation on every redirect hop).
- Student email is self-declared (only used for Reply-To/CC once sending exists).
- PDFs are only readable via the provider-extract fallback; visa rules are not modelled.
- Budgets/latency are initial assumptions until measured on the deployed instance.
