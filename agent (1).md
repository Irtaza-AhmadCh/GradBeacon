# GradBeacon --- Technical Agent Implementation Specification

**File:** `agent.md`\
**Status:** Implementation contract for the coding agent\
**Product:** GradBeacon\
**Scope:** US graduate-program discovery, funding/requirements research,
and optional professor discovery/outreach drafting\
**Primary product source:** GradBeacon Product Requirements Document
(PRD/PDR)\
**Architecture source:** GradBeacon End-to-End AI Orchestration and
Multi-Provider Workflow\
**Deployment target:** Cloud of Alternate Futures, subject to
runtime/network/secrets verification

------------------------------------------------------------------------

## 0. Instructions to the implementation agent

Implement the product and architecture described in this file. Treat the
two GradBeacon source documents as the business requirements baseline.
Do not silently broaden scope or weaken product safeguards.

Before coding: 1. Inspect the existing repository, framework, runtime,
and deployment instructions. Reuse the current stack if one exists; do
not rewrite a working app without a clear reason. 2. Verify the Cloud of
Alternate Futures runtime, outbound HTTPS, environment-variable/secrets
support, request limits, and persistence constraints. 3. Verify the
search API and at least one LLM API with real test requests and usable
quota. A consumer ChatGPT/Claude subscription is not evidence of API
access. 4. Deploy a trivial application early. Do not wait until the end
to discover deployment incompatibility. 5. If credentials or a
capability are unavailable, keep the product usable with curated
fallback data and document the limitation. Never fabricate successful
live operation.

Build a single maintainable application with clear modules, not a
distributed microservice system. The agent is allowed to make bounded
research decisions, but the orchestrator must enforce tool permissions,
budgets, validation, and stopping conditions.

## 1. Product contract and non-negotiable constraints

### 1.0 Who it is for and why it matters

GradBeacon is for prospective US graduate applicants---especially
international students who may not be able to afford an admissions
consultant. Researching each program's requirements, deadlines, tuition,
funding, and faculty can take hours and often means reconciling
scattered or conflicting official pages. GradBeacon reduces this manual
work by organizing source-backed findings, making unknowns visible, and
preparing next steps. It does not promise admission or replace official
university guidance.

### 1.1 Product goal

Help students considering US Master's and/or PhD study discover and
compare graduate opportunities, understand official requirements and
funding details, assess profile fit, and prepare next steps using
evidence-backed information.

### 1.2 Required product behavior

-   Support US-based graduate-program discovery.
-   Let the student choose Master's, PhD, or both.
-   Collect a compact, structured student profile.
-   Return 5--8 well-evidenced opportunities by default, with a hard
    maximum of 15; return fewer if fewer credible results are found.
    Stream results progressively when the UI/runtime supports it rather
    than waiting for every candidate.
-   Show profile-fit score and breakdown, reasons, evidence/source
    links, verification status, tuition/funding details when available,
    filters/sorting, and a concise application checklist.
-   Prefer official university, department, graduate school, funding,
    and faculty/lab sources.
-   Make live discovery and extraction the primary path when available;
    maintain clearly labelled curated fallback records.
-   Include professor/supervisor discovery and editable outreach drafts
    only after the core program workflow is reliable.
-   The student reviews, decides, and may edit an outreach draft. The
    backend sends one email only after the student explicitly approves
    the exact final message; it never submits applications.
-   Use local browser storage only for favorites and optional remembered
    profile. No login or permanent server-side student-profile database
    is required for the MVP.
-   Make the app usable when browser storage is unavailable, with
    session-only behavior where feasible.
-   Deploy to Cloud of Alternate Futures after verifying its
    capabilities.

### 1.3 Explicit non-goals

Do not build these unless the product owner explicitly changes scope: -
Multi-country coverage; initial market is the United States. - Permanent
student-profile storage, user accounts, or a multi-student dashboard. -
Automated applications, email sending without final student approval, or
follow-up. - Full application tracker/calendar, reply interpretation, or
document upload/parsing. - A custom crawler or unrestricted web
crawling. - Admission probability or claims that a student will be
accepted. - Unsupported visa/immigration rules. - A PDF report. -
Multiple model calls for the same fact without a defined validation
purpose.

### 1.4 Source-of-truth rules

-   Official source evidence is authoritative; an LLM answer alone is
    not evidence.
-   Missing is not false. Unknown GPA equivalence, deadline, tuition,
    funding, or eligibility must remain unknown.
-   Never represent missing tuition as zero.
-   Distinguish: (a) funding is mentioned/published, (b) funding appears
    available, and (c) this specific student's eligibility is confirmed.
    These are different states.
-   If official sources conflict, preserve the conflict and flag the
    field for verification; do not silently choose the convenient value.
-   A source that cannot be fetched is unavailable for current
    verification, not proof of ineligibility.
-   Fit score is not an admission probability and must be labelled
    accordingly.
-   Never invent a URL, deadline, fee, professor, publication, email
    address, research interest, or student achievement.
-   Label curated fallback results and cached data clearly, including
    freshness where known.

### 1.5 Differentiation

GradBeacon is **evidence-first, never fabricates**. Its differentiator
is not a chat interface: each important field has a verification state,
source URL, and supporting passage where available. Unknowns and
conflicts are visible instead of being confidently guessed. Put the
evidence panel prominently in the result UI.

## 2. Recommended architecture

Use a single backend with modular components:

1.  **Frontend** --- profile form, results, filters/sorting,
    evidence/source display, status trace, favorites, and optional
    professor/email-draft UI.
2.  **API layer** --- validates requests and returns typed responses.
3.  **Workflow orchestrator** --- owns state transitions, budgets,
    timeouts, retries, provider selection, fallback, and final response.
4.  **Research loop** --- bounded LLM-guided selection of the next
    permitted research action. It does not have unrestricted access to
    the internet or filesystem.
5.  **Shared tool layer** --- search, safe page fetch, readable-text
    extraction, retrieval, source metadata, cache, and deduplication.
6.  **Program research mode** --- program requirements, tuition,
    funding, deadlines, documents, and official links.
7.  **Professor research mode** --- official faculty/lab profiles,
    research interests, verified publications where available, official
    contact details, and optional draft preparation.
8.  **Structured extraction adapter** --- extracts claims into validated
    schemas from retrieved text.
9.  **Evidence validator and normalizer** --- checks schema, source
    identity, evidence references, duplicate records, conflicts, and
    unknowns.
10. **Deterministic matching/scoring module** --- evaluates hard
    constraints and preference fit; no LLM-generated score.
11. **Explanation/checklist/draft module** --- produces grounded prose
    from validated facts and score breakdown; deterministic templates
    are fallback.
12. **Curated fallback data** --- usable when live search, retrieval, or
    model calls fail.
13. **Observability** --- structured event logs and an optional UI trace
    of actual workflow events.

These are logical modules, not separate deployed services. Do not create
an independent AI agent for validation, scoring, deduplication, or
checklist formatting.

### 2.1 Agent naming and claims

Describe the system accurately as **one bounded tool-using research
agent with two task modes** (programs and professors), controlled by a
deterministic orchestrator. Shared search/fetch/retrieval components are
tools/services, not agents by themselves.

The research loop MUST use the selected LLM provider's **native
tool/function-calling API**. Do not simulate tool calling by asking the
model to print tool names in plain text and parsing them. The model
receives the current research state and the registered tool schemas,
then chooses the next permitted action. The orchestrator validates and
executes every call.

The agent's research tools are `search_web`, `fetch_page`,
`retrieve_evidence`, and `finish_research`. A post-research deliverable
action, `build_checklist`, may be exposed when appropriate.
`prepare_outreach_draft` is available only after the student approves a
shortlist/professor selection. Fact extraction is a controlled
orchestrator-invoked model step, not a tool the agent chooses. This
separation keeps the loop understandable and reliable.

A workflow is meaningfully agentic because the LLM observes a different
evidence state for each program and chooses a next action
accordingly---for example, searching for a funding page when funding is
missing, a deadline page when the deadline is missing, retrieving a
relevant faculty passage when research fit is unclear, or stopping when
evidence is sufficient or the budget is exhausted. A fixed workflow
would perform the same steps for every program even when the missing
facts differ; that wastes calls and cannot adapt to source-specific
gaps. Deterministic code remains responsible for validation, scoring,
permissions, and hard limits.

## 3. Research loop and control policy

### 3.1 Loop

1.  Receive a normalized request and a mode (`programs` or
    `professors`).
2.  Build the initial request state: profile preferences, target
    degree/field, collected facts, evidence gaps, visited URLs,
    call/page budget, and elapsed time.
3.  Invoke the configured LLM's native tool-calling API with the current
    state and the registered, mode-appropriate tool schemas.
4.  The LLM chooses one permitted next action: `search_web`,
    `fetch_page`, `retrieve_evidence`, or `finish_research`. The
    orchestrator validates the tool name and arguments before executing
    it.
5.  Execute the chosen tool; append a real workflow event and update the
    research state.
6.  After a page is fetched, the orchestrator invokes structured fact
    extraction against the relevant mode schema. Extraction is not
    selected as a tool by the agent. The extractor may only claim facts
    supported by the supplied page text and must return evidence
    references or unknowns.
7.  Validate extracted facts and recompute missing-evidence/gap state.
8.  Give the updated state back to the model. It may select a targeted
    follow-up search/fetch/retrieval or finish. It must not repeat
    unproductive actions indefinitely.
9.  When the model calls `finish_research`, or a hard budget is reached,
    normalize/deduplicate records, calculate deterministic fit, and
    progressively return credible results with source evidence and
    uncertainty.
10. After the student selects/approves a shortlist, build a checklist.
    If the student chooses a professor and explicitly requests outreach
    preparation, draft an editable email. The student reviews and may
    edit the exact draft, then explicitly approves one send; the
    deterministic backend sends it through the configured email provider
    and returns an auditable sent/failed status.

### 3.2 Native tool-calling contract

The LLM must use the provider's native tool/function-calling mechanism.
If the chosen provider does not support tool calling, select a
compatible model/provider or implement an equivalent structured
tool-call API; do not silently downgrade to free-form text parsing.

Required research tools:

-   `search_web(query, limit, domain_hints?)`: searches for candidate
    sources and returns normalized result metadata.
-   `fetch_page(url)`: safely retrieves an allowed public page and
    returns readable text plus source metadata.
-   `retrieve_evidence(query, filters?, top_k?)`: retrieves relevant
    passages from pages already fetched/cached (keyword retrieval is
    sufficient for P1; vector RAG is optional P3).
-   `finish_research(reason, unresolved_gaps?)`: ends the loop and
    returns the accumulated evidence and unresolved gaps.

Optional workflow tools, exposed only in the appropriate state:

-   `build_checklist(selected_program_ids)`: creates a source-grounded
    checklist after the student has approved the shortlist.
-   `prepare_outreach_draft(professor_id, selected_program_id?)`:
    creates an editable draft only after the student explicitly asks for
    it and approves the relevant selection. It does not send email.

Email sending is not an LLM research tool. The API layer may invoke a
separate allowlisted mail-provider adapter only after validating a
single-use final-approval token tied to the exact draft, recipient, and
student action.

Extraction is an orchestrator-invoked step after `fetch_page`, not a
selectable agent tool. It receives bounded page text and a strict
schema. The agent chooses search/fetch/retrieval/finish; deterministic
code validates and executes those tool calls. The orchestrator rejects
unknown tools, invalid arguments, disallowed domains/URLs, exhausted
budgets, and actions that are invalid for the current state.

### 3.2.1 Fixed workflow versus agent: why use an agent here?

Not every stage needs an agent. Scoring, validation, deduplication, and
checklist formatting are deterministic because they should be
predictable. The research step benefits from an agent because every
program's evidence gaps differ: one may lack a funding page, another a
deadline, another a current official requirements page. A bounded LLM
tool-calling loop can choose a targeted next action based on the current
evidence instead of blindly running every possible query. It completes a
useful workflow by returning an evidence-backed shortlist and checklist,
and---after explicit student approval---an editable outreach draft.

### 3.2.2 Human approval and uncertainty handoff

Human control is a first-class workflow state, not only a prohibition:

1.  Agent researches and returns evidence-linked candidates.
2.  Student reviews and approves a shortlist or selects a
    program/professor.
3.  If evidence is conflicting, missing, stale, or weak, the UI enters
    **Needs your decision / Verify this** state. Show the competing
    claims, sources, and the specific unresolved question. Ask the
    student to choose a source to investigate or explicitly continue
    with the uncertainty; never ask them to decide a fact the system can
    verify safely with another permitted search.
4.  After shortlist approval, the agent builds a checklist.
5.  If outreach is requested, the agent prepares an editable draft
    grounded in verified information.
6.  After the student explicitly approves the final draft, the
    deterministic mail adapter sends exactly that approved message once
    to the official published address and reports its outcome.

### 3.2.3 Progressive results and demo latency

-   Target first useful results within **45--60 seconds** on the demo
    path under normal provider conditions.
-   Return credible results progressively as each record is validated;
    do not wait for all 15. If the hosting platform does not support
    streaming, use short polling or staged responses.
-   Initial demo-path budget: keep the first candidate batch small (for
    example, 3--4 candidate programs), limit page fetches and model
    calls, and then continue research only while time/budget remains.
    Tune exact budgets using measured provider latency.
-   If the target is missed, show a clear "still researching" state and
    render any validated partial results. Do not present unvalidated
    partial extraction as verified.

### 3.3 Bounds

Define configurable limits, not unlimited autonomy. Start with
conservative defaults and tune after measuring real provider
latency/cost: - maximum research-loop iterations per request; - maximum
search calls; - maximum unique pages fetched; - maximum bytes/characters
per fetched page and per model context; - per-request and per-provider
timeout; - bounded retry count (normally one retry for malformed
structured output or transient failure); - overall request deadline; -
maximum model tokens/cost where supported; - maximum returned results:
15 credible program opportunities; - maximum professor results should be
configurable and visibly limited.

When a limit is reached, stop and return partial results with a reason.
Do not silently omit the fact that research was truncated.

### 3.4 Search strategy

-   Use US-focused queries derived from degree level, academic field,
    research interests, and program requirements.
-   Prioritize official university, graduate school, department,
    funding, and faculty/lab pages.
-   For program gaps, perform targeted searches such as official
    funding, tuition, admissions requirements, application deadlines,
    and required documents.
-   For professor mode, search official faculty/lab pages and official
    institutional profiles. Search publications only where a trustworthy
    source can be identified.
-   Use query/domain hints if supported by the chosen search provider;
    do not assume provider features before testing.
-   Return fewer credible results rather than fabricate or pad the list.
-   Do not treat search-result snippets as equivalent to verified page
    content.

## 4. Shared tool layer

Implement adapters behind interfaces so providers can be replaced
without rewriting business logic.

### 4.1 Search adapter

Responsibilities: - call one configured search provider; - normalize
provider results into a common schema; - preserve title, URL, snippet,
domain, and provider metadata when available; - apply query/page
budgets; - classify provider errors and timeouts; - never invent result
URLs.

Do not hard-code a provider until the key, quota, terms, response
schema, and latency have been tested.

### 4.2 Safe page fetch and text extraction

Responsibilities: - accept only HTTP(S) URLs; - reject localhost,
loopback, link-local, private-network and otherwise disallowed
destinations, including after DNS resolution; re-check redirects to
prevent SSRF; - enforce connect/read/overall timeouts, redirect limits,
response-size limits, and content-type allowlists; - use a readable-text
parser for HTML; preserve page title and relevant headings; - avoid
executing page scripts; - handle PDFs only if an available parser and
time budget justify it; otherwise mark unsupported; - preserve
canonical/final URL, retrieval timestamp, fetch status, content hash,
and a concise error category; - treat retrieved web content as untrusted
data, never as instructions to the agent.

If a page is inaccessible, preserve the known URL and mark facts as
unavailable/unverified.

### 4.3 Cache and deduplication

-   Deduplicate URLs after canonicalization and records by
    institution/program identity.
-   Cache search results and fetched page text when permitted by
    provider terms and runtime.
-   Store retrieval timestamp and content hash; don't describe stale
    cached evidence as freshly verified.
-   Keep cache bounded. A simple in-memory cache is acceptable for the
    MVP; a persistent external database is not required.
-   Cache must not become a student-profile store.

## 5. Optional P3: RAG / hybrid retrieval

**Do not build vector RAG for the MVP.** P1 uses keyword retrieval over
the small set of already-fetched page passages; this is enough to
support gap checks and simple evidence retrieval. RAG is a P3
enhancement, not a prerequisite for agentic behavior and not a
replacement for live official-source discovery. Implement vector
embeddings/indexing only after the deployed core workflow, fallback,
tests, and demo are stable. The original PRD sets a five-hour MVP
constraint; RAG must not jeopardize P0 functionality or deployment.

### 5.1 Purpose

Use RAG to retrieve relevant passages from official program/faculty
pages already discovered and fetched. It is most useful for: - long
pages with requirements distributed across sections; - finding
semantically related research interests and faculty expertise; -
answering follow-up questions against already retrieved source
material; - retrieving evidence to fill a known gap before initiating
another web search.

RAG does not establish that a fact is current or true. Live official
pages remain the preferred verification path for time-sensitive
deadlines, tuition, fees, funding, and admissions requirements.

### 5.2 Indexing pipeline

1.  Fetch and parse an allowed source.
2.  Split text into chunks that respect headings and section boundaries;
    avoid arbitrary tiny fragments.
3.  Attach metadata: `source_id`, `url`, `canonical_url`, `title`,
    `domain`, `institution`, `program_id` or `professor_id` when known,
    `document_type`, `section_heading`, `retrieved_at`, `content_hash`,
    and `source_status`.
4.  Embed chunks using a tested embedding model.
5.  Store vectors plus text and metadata in a bounded index.
6.  Update/re-index when the content hash changes; avoid duplicate
    chunks.

Do not index arbitrary user-uploaded or untrusted content in the MVP. If
web content includes prompt-injection text, treat it only as
evidence/data, not instructions.

### 5.3 Retrieval pipeline

-   Use hybrid retrieval: lexical/keyword matching plus vector
    similarity if both can be implemented reliably.
-   Filter by research mode and document type where appropriate.
-   Return the best few passages with source metadata and stable chunk
    IDs.
-   Optional reranking is a later optimization; add it only if
    evaluation shows meaningful gains.
-   Pass retrieved passages to the model with explicit instructions to
    use them as untrusted source data and cite supporting chunk/source
    IDs.
-   Verify that cited IDs were actually included in the model context
    and that the claim has a supporting passage.
-   If retrieval is weak or evidence is insufficient, run a targeted
    live search or report the gap.

### 5.4 Storage choice (P3 only) (P3 only)

Choose the simplest storage compatible with the verified deployment
environment: - a small local FAISS index or in-memory index for a
bounded demo corpus if the environment supports the required
dependencies and persistence; - PostgreSQL/pgvector only if a supported
database already exists and persistence is needed; - otherwise, omit the
vector store initially and use keyword retrieval over fetched passages.

Do not add a managed vector database, a new credential, or a complex
indexing service without a concrete need. Keep embeddings/indexing
optional behind an adapter.

### 5.5 RAG acceptance checks

-   Every retrieved chunk includes a real source URL and retrieval
    metadata.
-   A generated factual claim can be traced to a supplied passage.
-   The system labels unsupported/missing facts as unknown.
-   Re-indexing a changed page updates its evidence rather than
    duplicating it.
-   The RAG index can fail without taking down live search and curated
    fallback.
-   Current deadlines and fees are not presented as freshly verified
    solely because they appear in a stale index.

## 6. Program research mode

### 6.1 Program schema

Use typed models (Pydantic or equivalent) with explicit optional fields.
A recommended logical record:

``` json
{
  "program_id": "stable-internal-id",
  "institution_name": "string",
  "program_name": "string",
  "degree_level": "masters | phd",
  "field": "string",
  "official_program_url": "https://...",
  "department_url": "https://... or null",
  "requirements": [
    {
      "requirement": "string",
      "value": "string or null",
      "status": "verified | conflicting | not_found | unavailable",
      "evidence": [{"source_id": "id", "chunk_id": "id or null", "quote": "short supporting excerpt", "url": "https://..."}]
    }
  ],
  "tuition": {
    "amount": "number or null",
    "currency": "string or null",
    "period": "string or null",
    "status": "verified | conflicting | not_found | unavailable"
  },
  "funding": [
    {
      "type": "assistantship | scholarship | fellowship | other",
      "availability_statement": "string",
      "student_eligibility_status": "confirmed | not_confirmed | unknown",
      "status": "verified | conflicting | not_found | unavailable",
      "evidence": []
    }
  ],
  "deadline": {
    "date": "ISO date or null",
    "term": "string or null",
    "status": "verified | conflicting | not_found | unavailable"
  },
  "required_documents": [],
  "application_url": "https://... or null",
  "sources": [],
  "retrieved_at": "timestamp",
  "data_freshness": "live | cached | curated",
  "verification_status": "verified | partially_verified | needs_verification | fallback"
}
```

This is a logical contract; adjust exact field types to the existing
codebase while preserving the semantics. Do not require a fact to exist
when the source doesn't provide it.

### 6.2 Extraction requirements

The extraction model: - receives bounded source text and the schema; -
extracts only claims supported by that source; - returns evidence
pointers/short excerpts and explicit unknowns; - does not score or rank
programs; - does not infer eligibility from a generic funding
statement; - does not treat a search snippet as a verified page claim.

### 6.3 Program gap-filling

After validation, compute gaps deterministically. Examples: - no tuition
evidence → search official tuition/graduate cost page; - no funding
evidence → search official department/university funding page; - no
deadline evidence → search official admissions deadline page; - no
required-documents evidence → search official application requirements
page.

The LLM may choose which gap to investigate next, but the orchestrator
checks the action against the remaining budget and mode permissions.

## 7. Professor research mode

Professor mode is a second configuration of the same research loop and
shared tools, not a separate free-running agent.

### 7.1 Professor schema

Include: - name; - title/department when available; - institution; -
official profile URL; - lab URL when available; - research interests
supported by official text; - verified publications only when a reliable
source is available; - official email only when published by an official
source; - research-fit explanation grounded in student interests and
evidence; - intake status only if explicitly sourced; - field-level
verification status and source evidence.

### 7.2 Behavior

-   Run after program results are available or when explicitly
    requested.
-   Prefer faculty and lab pages associated with relevant
    institutions/programs.
-   Compare student interests with sourced faculty research text.
-   Do not invent email addresses or publication titles.
-   If no official email is found, link the department/admissions office
    when possible.
-   Keep professor result count configurable and bounded.
-   If the core program workflow is unstable, disable professor mode or
    serve a clearly labelled curated example rather than compromising
    the core flow.

## 8. Deterministic matching and scoring

The backend owns hard constraints, score calculation, ranking,
deduplication, and verification-state rules. No LLM should invent a
score.

### 8.1 Scoring principles

-   Store scoring weights in one versioned configuration.
-   Make score breakdown visible to the user.
-   Score preference fit separately from explicit eligibility checks.
-   Report known matches, known gaps, and unknown requirements.
-   Unknowns must not be silently treated as satisfied or as automatic
    failures.
-   If grading scale/context is unknown, do not pretend GPA comparison
    is exact.
-   Do not label a fit score as an admission chance.
-   Sorting/filtering of already returned records must work locally
    without additional AI calls.

The source PRD leaves exact scoring weights as an open decision.
Implement a transparent, documented initial configuration and keep it
easy to tune; do not present the weights as empirically validated.

### 8.2 Verification states

Use a small, consistent set such as: - `verified`: claim supported by a
retrieved source and validation passed; - `partially_verified`: some
important fields are supported, but gaps remain; - `conflicting`:
credible sources disagree on a field; - `not_found`: search completed
but no supporting information was found; - `unavailable`: a source could
not be accessed or a provider failed; - `curated_fallback`: result comes
from the curated dataset, not current live verification.

Avoid a single green "verified" badge if only one field is verified.
Prefer field-level states plus a summary status.

## 9. Explanations, checklist, and outreach drafts

### 9.1 Explanations

Use a second provider only if credentials, quota, terms, latency, and
reliability have been tested and it materially improves the output.
Provider B receives only normalized facts, scores, and relevant
evidence. It cannot modify scores or introduce new facts.

If Provider B is unavailable, use deterministic templates from the score
breakdown and verified fields.

### 9.2 Checklist

Include only sourced items: - official application/program link; -
required documents; - published deadlines and fees; - funding
application details; - items that still need verification.

Never invent missing checklist requirements.

### 9.3 Outreach email drafts

-   Use only student-provided facts and verified professor/research
    evidence.
-   Draft is editable and includes official source links where useful.
-   Do not invent a paper, professor interest, achievement, or personal
    connection.
-   Include a reminder to verify contact expectations.
-   The student reviews and may edit the exact draft. A deterministic
    mail adapter sends it only after explicit final approval; bind that
    approval to the draft hash and official published recipient, consume
    it after one attempt, and surface the provider's sent/failed result.
-   If grounding is insufficient or the drafting provider fails, omit
    the draft or use a safe minimal template.

## 10. API and internal contracts

Adapt route names to the existing repository, but preserve clear typed
request/response boundaries.

### 10.1 Suggested HTTP endpoints

-   `GET /health` --- liveness and safe dependency/configuration status;
    never return secrets.
-   `POST /api/research/programs` --- validate profile and start bounded
    program research.
-   `POST /api/research/professors` --- research professors for selected
    institutions/programs and interests.
-   `POST /api/outreach/draft` --- produce a draft from validated
    professor facts and student-supplied facts.
-   `POST /api/outreach/send` --- validate a final, user-approved draft
    and send it once through the configured transactional mail adapter.
-   Optional `GET /api/research/{request_id}/events` or an equivalent
    streaming mechanism for actual workflow events, only if supported by
    deployment and time budget.

Avoid exposing provider keys, raw stack traces, internal prompts, or
sensitive logs in API responses.

### 10.2 Request contract

The profile may include: - degree pathway (`masters`, `phd`, or both); -
academic field and optional specialization; - undergraduate GPA and
grading scale/context if known; - academic background/degree status; -
research interests; - preferred locations or institutions, if
collected; - funding/tuition preferences and other product-approved
preferences.

Validate and normalize input. Do not require optional details. Do not
store the profile server-side after the request. Minimize profile data
sent to model providers.

### 10.3 Response contract

Return: - results (5--8 by default, up to 15 credible programs
maximum); - score and score breakdown; - known matches/gaps/unknowns; -
field-level evidence and official source links; - verification and
freshness states; - checklist; - workflow summary and fallback notice; -
warnings and non-fatal errors.

Never return a fabricated success payload just to satisfy the frontend.

### 10.4 Provider adapters

Define interfaces for: - `SearchProvider.search(...)` -
`PageFetcher.fetch(...)` - `LLMProvider.extract(...)` - optional
`LLMProvider.explain(...)` / `draft(...)` - optional
`EmbeddingProvider.embed(...)` - optional `VectorIndex.upsert(...)` /
`retrieve(...)`

Use one provider initially if that is what can be verified. Provider A
and Provider B are logical task roles, not a requirement for two
vendors. Do not send identical tasks to multiple providers without a
clear validation purpose.



## 11. Security, privacy, and untrusted content

-   Keep keys in environment variables or the platform's secret manager;
    never expose them to frontend bundles, source control, responses, or
    logs.
-   Provide `.env.example` with variable names only and safe
    placeholders; ensure `.env` is ignored by version control.
-   Verify required environment variables at startup and report missing
    variable names without printing values.
-   Use HTTPS and timeouts for outbound requests.
-   Restrict URL fetching and redirects to prevent SSRF; reject
    private/internal addresses and unsupported protocols.
-   Treat all web pages, snippets, and RAG chunks as untrusted data.
    They may contain prompt injection. They must never override system
    instructions or tool permissions.
-   Separate instructions from retrieved source text in prompts.
-   Do not give the LLM shell, filesystem, unrestricted network,
    deployment, or application-submission tools. Email sending remains a
    deterministic, approval-gated API action, not an LLM tool.
-   Validate tool names and arguments against allowlists and typed
    schemas.
-   Avoid logging full student profiles, API keys, full source
    documents, or unnecessary personal data.
-   Keep profile/favorites storage local and opt-in as specified by the
    PRD.
-   Apply bounded request sizes and rate limits where the runtime
    supports them.
-   Validate output links and escape rendered content to prevent unsafe
    HTML/script injection.
-   No email without final user approval; no application submission.

## 12. Failure handling

  ---------------------------------------------------------------------
  Failure                            Required behavior
  ---------------------------------- ----------------------------------
  Search API unavailable/invalid key Use curated fallback if available;
                                     label it; expose a useful
                                     non-fatal status

  Page fetch times out/blocked       Keep known source URL; mark fields
                                     unavailable/unverified; continue
                                     other sources

  Invalid LLM JSON/schema            One bounded retry if budget
                                     permits; then skip/mark extraction
                                     unavailable

  Extraction has unsupported claim   Reject or mark unsupported; never
                                     treat it as verified

  Conflicting official sources       Preserve claims/sources and mark
                                     conflict

  RAG/embedding/index unavailable    Continue with live search, fetched
                                     text, and/or keyword retrieval

  Explanation provider unavailable   Use deterministic templates

  Outreach generation fails          Omit draft or show a safe minimal
                                     template

  No credible programs found         Return an honest empty state; do
                                     not pad results

  Curated fallback used              Clearly label source mode and
                                     freshness; never claim it is a
                                     live result

  Missing environment variable       Fail safely with a clear
                                     configuration error; do not print
                                     secret values

  Browser local storage unavailable  Continue in session without
                                     persistent favorites/profile where
                                     feasible

  Budget/deadline exhausted          Stop and return partial results
                                     with a reason
  ---------------------------------------------------------------------

## 13. Observability and visible agent trace (P1, not optional)

Emit structured events with: - request/workflow ID; - mode; - stage/tool
name; - safe action summary; - source count and page count; - selected
gap/follow-up reason; - provider/task duration and error category; -
extraction/validation counts; - number of live versus fallback
results; - stop reason and elapsed time.

Do not log API keys, full profile payloads, or unnecessary personal
information.

The UI may show a live trace such as: - "Searching official program
pages" - "Retrieved 4 candidate sources" - "Funding evidence missing for
2 programs" - "Searching official funding pages" - "Validated 6 program
records" - "Stopped: evidence sufficient / budget reached"

These messages must be generated from real backend events, not simulated
by an LLM. Do not expose chain-of-thought or private model reasoning;
show concise action summaries and reasons only.

## 14. Testing and acceptance criteria

### 14.1 Functional cases

Test: - Master's only, PhD only, and both; - missing optional profile
fields; - unknown GPA grading context; - no credible results; - tuition
not found; - funding mentioned but student eligibility unknown; -
duplicate program from multiple URLs; - conflicting official source
statements; - professor page with no published email; - source fetch
failure; - live search and AI provider failure; - browser local storage
disabled; - backend restart; - missing environment variable; - RAG
unavailable or stale index (if RAG implemented).

### 14.2 Agent behavior tests

At minimum, demonstrate: 1. The agent identifies a missing evidence
field. 2. It chooses a relevant permitted follow-up action. 3. The tool
is actually called. 4. The returned evidence changes the research state.
5. The agent stops when sufficient or when a limit is reached. 6. It
does not loop indefinitely or call a forbidden tool. 7. If the follow-up
fails, it reports uncertainty instead of fabricating a result.

Use a deterministic/mock tool environment for repeatable tests, plus a
small set of real-source smoke tests.

### 14.3 Evidence and safety tests

-   No invented source URLs.
-   Each verified factual claim links to a source and supporting
    evidence.
-   No fabricated professor emails/publications.
-   No claim that fit score is admission probability.
-   No confirmed funding eligibility without explicit evidence.
-   No email send without final user approval and no application
    submission.
-   No API key in frontend assets, API responses, or logs.
-   Fallback is clearly labelled.
-   Web-page prompt injection cannot expand tool permissions or override
    policies.
-   Unsafe/private-network URLs are rejected by the fetcher.

### 14.4 MVP acceptance

The app is demo-ready when a student can enter a profile, receive live
or clearly labelled fallback program results, understand why results
match, inspect official evidence and uncertainty, filter/sort locally,
save a favorite when browser storage permits, and see graceful behavior
under provider failure. Professor search and outreach drafts are
included only if they do not destabilize this path.

### 14.5 Mandatory pre-demo smoke-test checklist

Run and record this checklist before the event demo:

-   [ ] Test live search and official page retrieval for **3--4 named
    graduate programs** selected and verified by the implementation
    agent. Record each program's official URL and the actual fields
    successfully verified; do not rely on search snippets alone.
-   [ ] Confirm the real tool-calling loop makes at least one
    evidence-gap-driven follow-up decision.
-   [ ] Confirm first useful validated results arrive within the 45--60
    second target, or document measured latency and ensure partial
    results render.
-   [ ] Deliberately use an invalid search API key in a safe test
    environment. Confirm the system fails gracefully and does not leak
    the key.
-   [ ] Confirm curated fallback labels are visible.
-   [ ] Confirm cached demo replay labels and original capture time are
    visible.
-   [ ] Confirm conflicting/unknown facts show **Needs your decision /
    Verify this** UI.
-   [ ] Confirm shortlist approval gates checklist/outreach actions;
    sending requires review/edit opportunity and final approval bound to
    the draft and official recipient.
-   [ ] Confirm an approved email is sent once, an unapproved or altered
    draft cannot send, and no application is submitted automatically.
-   [ ] Confirm the deployed primary instance and a separately deployed
    backup instance both load and have configured secrets.
-   [ ] Confirm no keys or private student data appear in frontend
    bundles, API responses, or logs.

## 15. Implementation priority

The PRD specifies a five-hour MVP constraint. Preserve that constraint
when prioritizing. The workshop also notes that a basic build/deployment
can take 10--15 minutes, so deployment is the first milestone, not a
final task.

### P0 --- First 10--15 minutes: deploy and verify

1.  Inspect the existing repository and confirm the stack.
2.  Deploy a trivial app from the GitHub repository to Cloud of
    Alternate Futures using **GitHub repo → Add Service → configure
    environment variables → deploy** (use the platform's current UI
    labels if they differ).
3.  Verify public health route, outbound HTTPS, environment
    variables/secrets, request limits, and runtime behavior.
4.  Verify one search API key and one LLM API key/quota with real
    requests.
5.  Create and verify a second deployed backup instance with the same
    application version and separately configured secrets. Do not wait
    until demo day.

### P1 --- Must work for the core demo

1.  Profile form and typed API boundary.
2.  Curated fallback data and visible fallback state.
3.  Native LLM tool-calling loop with `search_web`, `fetch_page`,
    `retrieve_evidence`, and `finish_research`.
4.  Orchestrator-invoked structured extraction after fetch.
5.  Deterministic evidence validation, normalization, deduplication, and
    fit scoring.
6.  5--8 strong results by default, up to 15 maximum, with progressive
    rendering.
7.  Real execution trace and human-approval/uncertainty UI.
8.  Safe failure handling and smoke tests.

### P2 --- Add only after P1 is reliable

1.  Professor research mode reusing the same loop and tools.
2.  Student-approved, editable outreach drafts with one-time,
    approval-gated sending; optional copy/mailto.
3.  Verified-deadline `.ics` export.
4.  Demo replay using a cached real run with timestamp and visible
    label.
5.  Extra UI polish and expanded tests.

### P3 --- Optional technical enhancement

1.  Vector RAG/hybrid semantic retrieval over fetched official pages.
2.  Reranking.
3.  MCP integration only if a concrete interoperability requirement
    emerges.

If time is constrained, disable/defer P2/P3 rather than weakening
evidence quality, fallback, deployment, privacy, or human approval.

## 16. Configuration and secrets

Use environment variables appropriate to the chosen providers. Names are
examples and must be adapted to actual adapters:

``` dotenv
SEARCH_API_KEY=
LLM_API_KEY=
LLM_MODEL=
# Optional, only if a second provider is verified:
LLM_EXPLANATION_API_KEY=
LLM_EXPLANATION_MODEL=
# Optional RAG:
EMBEDDING_API_KEY=
EMBEDDING_MODEL=
```

Do not assume these exact variables are supported by a provider.
Document each required variable in the README. Use platform secret
management in deployment. Do not commit credentials.

## 17. Six-minute demo flow

Use a rehearsed flow with one known-good student profile and a
pre-warmed cached run available as a clearly labelled backup.

1.  **0:00--0:30 --- Problem:** international applicants spend hours
    reconciling program requirements, funding, and deadlines across
    scattered pages.
2.  **0:30--2:30 --- Live agent run:** submit the profile; show the real
    trace. Highlight a program whose funding/deadline evidence is
    missing and show the agent choose a targeted tool call.
3.  **2:30--3:30 --- Evidence-first result:** open a card's evidence
    panel; show source URL, supporting passage, field-level
    verification, and one honest unknown or conflict.
4.  **3:30--4:30 --- Human + AI:** show **Needs your decision / Verify
    this** for a real conflict or uncertainty. The student chooses
    whether to verify another source or continue with the warning, then
    approves the shortlist.
5.  **4:30--5:00 --- Deliverable:** generate a checklist; if professor
    mode is stable, request an outreach draft, edit/review it, explicitly
    approve it, and show the sent/failed outcome. Do not submit an
    application.
6.  **5:00--5:30 --- Failure resilience:** demonstrate a prior safe test
    or controlled invalid-key mode and show clearly labelled
    fallback/cached replay. Never expose the actual production key.
7.  **5:30--6:00 --- Impact and measured result:** report actual run
    metrics: N programs, X minutes, Y official sources, and
    live/cached/fallback state. Close with the evidence-first
    differentiator.

Do not spend demo time on vector-database internals or claim RAG if it
is not implemented. If professor mode or outreach is not stable, use the
remaining time to show the checklist and human approval gate.

## 18. Definition of done

The implementation is complete when: - The repository's actual framework
and deployment environment are verified and documented. - A trivial
deployment succeeded early and the finished app is deployed to the
required target. - Program search works live or returns clearly labelled
curated fallback data. - At least one real agentic follow-up behavior is
demonstrated end-to-end. - Evidence, uncertainty, source freshness, and
fallback status are visible. - Scoring is deterministic, explainable,
and not an admission prediction. - Professor mode/drafting, if included,
reuses the same tools and preserves manual approval. - Failures are
bounded and do not result in fabricated facts. - Security, privacy, and
acceptance tests pass. - README documents architecture, setup,
environment variables, provider verification, limitations, and demo
flow.

**Final implementation principle:** Build a bounded research agent, not
an unrestricted autonomous system. Use AI to choose research actions,
extract and explain source-backed information; use deterministic code
for permissions, evidence validation, scoring, and limits; and keep the
student in control of consequential decisions.

## Source alignment note

The PRD remains the product authority: US graduate-program discovery,
evidence-backed requirements/funding, deterministic fit scoring,
local-only optional profile/favorites, no automatic application
submission, and final-approval-gated email sending remain binding. The
orchestration document remains the architecture authority for a bounded
orchestrator, specialized model tasks, validation, normalization,
scoring, provider adapters, and fallback. The workshop-driven additions
in this file---native tool calling, visible human approval, cached demo
replay, latency target, and `.ics` export---must be implemented without
weakening those source constraints.
