# GradBeacon --- Product Requirements Document (PRD/PDR)

**Document status:** Requirements baseline for hackathon MVP\
**Product:** GradBeacon\
**Version:** 1.0\
**Scope:** United States graduate opportunities\
**Primary constraint:** Five-hour implementation window\
**Required deployment target:** Cloud of Alternate Futures (exact
platform URL/runtime capabilities must be verified before
implementation)\
**Audience:** Product team, frontend/backend developers, AI integration
owner, hackathon judges

------------------------------------------------------------------------

## 1. Executive Summary

GradBeacon is an AI-assisted graduate-opportunity discovery tool for
students who have completed or are completing a bachelor's degree and
are considering master's programs, PhD programs, or both in the United
States.

The tool automates the research-heavy parts of the process: discovering
programs, extracting official requirements, identifying funding
information, finding relevant professors or potential supervisors,
comparing opportunities against a student's profile, and preparing
editable outreach email drafts. The student remains in control: they
inspect evidence, review warnings and checklists, choose which
opportunities to pursue, and explicitly approve any agent-assisted email
send.

The MVP prioritizes evidence-backed recommendations and transparent
uncertainty over maximizing the number of results. It combines live
discovery and extraction from official sources with a curated fallback
dataset. It must never present a missing or uncertain fact as verified.

## 2. Product Vision

**Vision:** Help students make better-informed graduate-study decisions
by automating opportunity research while keeping consequential decisions
with the student.

### 2.1 Core value proposition

-   Reduce the manual effort required to discover and compare graduate
    programs.
-   Surface funding opportunities and distinguish confirmed
    source-backed information from unconfirmed possibilities.
-   Explain why each opportunity matches the student's profile.
-   Help students identify relevant professors and draft personalized,
    editable outreach emails.
-   Give students official source links, verification labels, and
    application checklists so they can independently validate details
    and act.

### 2.2 Human-in-the-loop principle

AI researches, extracts, organizes, ranks, drafts, and may send an
outreach email only after the student has reviewed the exact final
message and explicitly approved sending it. The product never submits
applications.

## 3. Goals and Non-Goals

### 3.1 MVP goals

1.  Support US-based graduate-program discovery.
2.  Support Master's, PhD, or both as independently selectable pathways.
3.  Collect a compact, structured student profile.
4.  Find and rank up to 15 credible opportunities per search; return
    fewer if fewer credible results are found.
5.  Show explainable profile-fit scores, relevant evidence, tuition and
    funding details when available, and clear verification states.
6.  Include filters and sorting.
7.  Include a concise application checklist and official links for each
    opportunity.
8.  Include lightweight professor/supervisor discovery, editable
    outreach email drafts, and student-approved email sending by the
    agent if the core workflow is stable.
9.  Provide graceful fallback to a curated dataset when live discovery
    or AI services fail.
10. Deploy to Cloud of Alternate Futures after its runtime, outbound
    network, and secret-management capabilities are verified.

### 3.2 Non-goals for the five-hour MVP

-   Supporting 20 countries at launch; the launch market is the United
    States.
-   User accounts, server-side student-profile storage, or a cloud
    database.
-   Automatically submitting applications or sending emails without a
    student's final review and explicit approval.
-   A full application tracker, deadline calendar, email reply
    interpreter, or follow-up agent.
-   A counsellor/agency multi-student dashboard.
-   CV/transcript upload and automated document parsing.
-   A claim that a student will be admitted or is definitively eligible
    when the evidence does not establish it.
-   Building a custom web crawler or attempting unrestricted crawling.
-   Producing a PDF report.
-   Implementing every possible visa, immigration, or post-study-work
    rule in the first version.

## 4. Users and Use Cases

### 4.1 Primary user

A student with a completed or in-progress bachelor's degree who is
exploring graduate study in the United States, including international
applicants.

### 4.2 Primary user stories

-   As a student, I want to enter my academic background and interests
    so that the tool can identify relevant US graduate programs.
-   As a student, I want to choose Master's, PhD, or both so that I can
    compare the appropriate pathways separately.
-   As a student, I want to see funding and tuition information so that
    affordability influences my shortlist.
-   As a student, I want to understand why a program matches me and
    which requirements need checking.
-   As a student, I want official source links so that I can validate
    details and apply directly.
-   As a student, I want an application checklist so that I can prepare
    the next steps.
-   As a student, I want to discover professors whose research aligns
    with my interests.
-   As a student, I want an editable outreach email draft that the
    agent sends for me only after I review, edit, and approve it.
-   As a student, I want to save favorites and optionally remember my
    profile on this device without creating an account.

## 5. Scope and Functional Requirements

### 5.1 Landing page

**FR-01:** The landing page must explain GradBeacon's purpose and
human-in-the-loop approach.

**FR-02:** The primary call to action must be "Find Opportunities,"
opening the structured profile form.

**FR-03:** The visual style should be professional and academic: clean
layout, whitespace, navy/blue accents, readable typography, and
restrained AI-product styling.

### 5.2 Student profile

Required fields: - Desired degree pathway: Master's, PhD, or both. -
Intended field/discipline. - Current or completed qualification. -
GPA/CGPA and grading context where provided. - Destination country,
initially US-focused.

Optional fields: - Research interests. - Thesis, projects, or relevant
research experience. - Work experience. - Skills. - English-language
test scores. - Budget or tuition preferences. - Funding preferences.

**FR-04:** The form must distinguish required from optional fields.

**FR-05:** Students may skip optional questions. Missing information
must be treated as unknown, not as a negative value or proof of
ineligibility.

**FR-06:** The MVP does not require CV upload.

**FR-07:** The student may choose "Remember my profile on this device."
If selected, the profile is stored in browser-local storage; otherwise,
it is kept for the current session only.

**FR-08:** Provide a clear way to clear the locally remembered profile.
Explain that local browser data is device-specific and may be visible to
other users of that browser/device.

### 5.3 Discovery and program results

**FR-09:** The system must discover and extract program details using
live search and accessible official university, department, program, and
funding pages where feasible.

**FR-10:** A curated fallback dataset must be available when live
search, page retrieval, or AI services fail.

**FR-11:** The system must display up to 15 relevant opportunities per
search and must not invent extra results to reach a target count.

**FR-12:** Master's and PhD pathways must be searched and ranked
independently when both are selected.

**FR-13:** Direct-entry PhD opportunities after a bachelor's degree may
be included only when published requirements support that pathway.

**FR-14:** Each program card should show, when available: - Program and
institution name. - Degree level and field. - Overall profile-fit
score. - Fit-score breakdown. - Tuition information. - Funding
information and funding-status distinction. - Eligibility/verification
status. - Key requirements and documents. - Deadlines when sourced. -
Official source links. - Retrieval or last-verified date when
available. - A concise application checklist.

**FR-15:** The results page must state when results are from fallback
data and must not imply the fallback data was freshly verified.

### 5.4 Funding and affordability

**FR-16:** Funding information must distinguish: 1. Funding supported by
official source evidence. 2. Potential funding not confirmed for the
student.

**FR-17:** The tool must not claim that a student qualifies for a
scholarship, assistantship, fellowship, tuition waiver, or other funding
solely because the funding exists.

**FR-18:** Funding amount, tuition, and fees must not be fabricated.
Unknown amounts should be labeled "Not found" or "Needs verification."

**FR-19:** When amounts can be compared reliably, users may sort by
funding amount or tuition. The interface must not treat missing amounts
as zero.

### 5.5 Ranking, score, filtering, and sorting

**FR-20:** Default result order is best overall profile fit.

**FR-21:** The overall fit score is not an admission probability.

**FR-22:** Show a score breakdown across dimensions such as: - Academic
eligibility fit. - Research fit. - Funding fit. - Other stated
preferences.

**FR-23:** The score should be explainable and use deterministic rules
for known fields. AI may extract/interpret evidence and write concise
explanations, but must not silently override deterministic eligibility
rules.

**FR-24:** If key data is missing or contradictory, mark the relevant
dimension unknown or uncertain and explain why.

**FR-25:** Provide filters and sorting where data supports them: -
Degree level. - Field. - Funding status. - Fully funded opportunities
first where source evidence supports the classification. - Match
score. - Funding amount. - Tuition fees, low to high or high to low.

**FR-26:** Filters and sorting must operate on the already retrieved
result set without requiring a new AI call.

### 5.6 Verification and uncertainty

Use clear labels such as: - **Verified from official source:** the
relevant source was accessible and checked. - **Needs verification:** a
requirement or eligibility condition is missing or ambiguous. - **Source
unavailable:** the official source could not be accessed during the
search. - **Curated fallback:** result came from the fallback dataset,
with last-verified date when available.

**FR-27:** Potentially suitable programs with incomplete requirements
may be shown with a clear warning; confirmed matches should be
prioritized.

**FR-28:** Conflicting or ambiguous source details must be flagged
rather than guessed.

**FR-29:** The tool must not fabricate program names, source URLs,
professor papers, emails, deadlines, fees, funding amounts, or student
achievements.

### 5.7 Application checklist

**FR-30:** Each opportunity should include a concise checklist generated
from retrieved evidence, covering relevant requirements, documents,
deadlines, fees, and official application links when available.

**FR-31:** Unknown checklist items must be labeled as unavailable or
needing verification, not filled using assumptions.

### 5.8 Professor/supervisor discovery

This feature is in the intended initial scope but is secondary to the
core program discovery workflow under the five-hour limit.

**FR-32:** Discover professors or potential supervisors using official
university faculty, department, and lab pages, plus accessible
publication/research information.

**FR-33:** Rank professor matches primarily by research fit and explain
the evidence behind the match.

**FR-34:** Show professor name, title/department, research interests,
relevant published work when verified, official profile/lab link, and
match explanation when available.

**FR-35:** Show student intake/accepting-students status only if
official evidence supports the claim. Otherwise label it unknown or not
confirmed.

**FR-36:** Show email addresses only when published on an official
university page. Never infer or guess an email address.

**FR-37:** If no official email is available, link to the relevant
department or admissions/international office when possible.

### 5.9 Outreach email drafting

**FR-38:** Generate editable, personalized outreach drafts using
verified professor research evidence and facts supplied by the student.

**FR-39:** Drafts must not invent a paper, professor interest, student
achievement, or personal connection.

**FR-40:** Include official professor/source links where appropriate and
remind the student to verify contact expectations.

**FR-41:** The student must review and may edit the exact final draft
before explicitly approving a single send. The system must not send an
email before this final approval.

**FR-42:** The system must send only to an official, source-published
professor email address after final approval. A copy action or mailto
link may be provided as an optional alternative. The UI must show a
clear sent/failed status and must never retry a send without renewed
student approval.

### 5.10 Favorites and local profile memory

**FR-43:** Students can save and unsave favorite opportunities in
browser storage.

**FR-44:** No user account or server-side profile database is required.

**FR-45:** The app must work when local storage is unavailable or
disabled, with a graceful session-only fallback where feasible.

### 5.11 Failure handling

**FR-46:** If live discovery fails, show clearly labeled curated
fallback opportunities when available.

**FR-47:** If an AI provider fails, use deterministic logic and
cached/curated data where possible; do not make the entire product
unusable because an optional AI task failed.

**FR-48:** If a source page is inaccessible, preserve the source link if
known and label the detail as unverified/unavailable.

**FR-49:** Provide useful empty/error states rather than fake results or
unbounded loading.

## 6. AI and Technical Architecture Requirements

### 6.1 Hybrid approach

-   Deterministic code handles input validation, deduplication, hard
    constraints, score calculation, sorting, and verification-state
    rules.
-   AI handles structured extraction from retrieved text, interpretation
    of research fit, concise explanations, and email drafting. Email
    sending is executed by application code only after the student
    approves the final draft.
-   Official sources remain the source of truth.
-   Multiple AI providers may be used for distinct tasks, but only if
    API credentials, quotas, terms, and latency are confirmed early.
-   Do not assume a consumer ChatGPT or Claude subscription provides API
    credits.

### 6.2 Suggested orchestration sequence

1.  Validate and normalize student profile.
2.  Search for relevant official pages and candidate programs.
3.  Retrieve accessible pages within bounded timeouts.
4.  Extract structured program/funding facts with source references.
5.  Validate required fields, normalize data, deduplicate, and mark
    uncertainty.
6.  Apply deterministic hard constraints and fit scoring.
7.  Rank and return up to 15 credible opportunities.
8.  Generate concise explanations and application checklists from
    validated facts.
9.  Optionally run professor discovery, email drafting, and
    student-approved email sending after core results are available.
10. Return results with source links, timestamps, warnings, and fallback
    status.

### 6.3 Provider resilience

-   Use provider adapters so the app is not coupled to one AI vendor.
-   Use one provider for extraction and a different provider for
    research-fit explanations or drafting only if this improves
    reliability and can be implemented within the time limit.
-   Avoid sending the same task to multiple providers without a specific
    reason.
-   Enforce request timeouts, bounded retries, and structured output
    validation.
-   If an AI task fails, degrade gracefully to source-backed raw
    details, deterministic scoring, and fallback data.

### 6.4 Secrets and deployment

-   API keys must be stored in server-side environment variables, never
    committed to source control or exposed in browser code.
-   Confirm Cloud of Alternate Futures supports the chosen backend
    runtime, environment variables/secrets, outbound HTTP requests, and
    static/frontend hosting.
-   Do not assume Vercel or any Vercel-specific runtime.
-   If the platform cannot support secure server-side calls, switch to a
    compatible backend or curated/demo mode rather than expose secrets.

## 7. Data Model (MVP)

### StudentProfile

-   degree_paths: Master's / PhD
-   field
-   qualification
-   GPA/CGPA
-   grading_context (optional)
-   research_interests (optional)
-   projects_or_thesis (optional)
-   experience (optional)
-   skills (optional)
-   language_test_scores (optional)
-   budget_preferences (optional)
-   funding_preferences (optional)

### ProgramOpportunity

-   id
-   institution_name
-   program_name
-   degree_level
-   field
-   official_program_url
-   official_funding_url (optional)
-   tuition_amount/currency/period (optional)
-   funding_summary
-   funding_evidence_status
-   requirements (structured where available)
-   required_documents (optional)
-   deadline (optional)
-   eligibility_status
-   verification_status
-   source_retrieved_at (optional)
-   last_verified_at (optional)
-   fallback_record (boolean)
-   raw_source_excerpt or evidence references (where feasible)

### MatchResult

-   opportunity_id
-   overall_fit_score
-   academic_fit
-   research_fit
-   funding_fit
-   preference_fit
-   known_gaps
-   explanation
-   score_version

### ProfessorMatch

-   name
-   title/department (optional)
-   institution
-   official_profile_url
-   lab_url (optional)
-   research_interests
-   verified_publications (optional)
-   official_email (optional)
-   research_fit_explanation
-   intake_status (only if sourced)
-   verification_status

### Local-only state

-   favorites
-   remember_profile preference
-   locally remembered profile, only if opted in

## 8. Non-Functional Requirements

-   **Responsive:** Usable on desktop and mobile browsers.
-   **Privacy-conscious:** No account or server-side profile persistence
    in the MVP.
-   **Secure:** Secrets are server-side; no secrets in frontend bundles
    or logs.
-   **Explainable:** Recommendations include reasons and sources.
-   **Resilient:** Live search and AI failure must not necessarily block
    curated results.
-   **Bounded:** Search and retrieval use explicit timeouts and limits.
-   **Accessible:** Labels, readable contrast, keyboard-usable controls,
    and clear status text should be used where feasible.
-   **Cost-aware:** Avoid duplicate AI calls and use deterministic logic
    for tasks that do not require a model.
-   **Auditable enough for a demo:** Retain source URLs and
    retrieval/verification metadata with each result where available.

## 9. MVP Acceptance Criteria

The MVP is acceptable for demo when:

1.  A user can open the landing page and start a search.
2.  A user can complete the required profile fields and choose Master's,
    PhD, or both.
3.  The app returns program opportunities from live discovery or clearly
    labeled fallback data.
4.  Results include official source links and visible verification
    states.
5.  Results are ordered by profile fit and show a score breakdown that
    is clearly not an admission probability.
6.  Funding and tuition fields distinguish unknown data from zero
    values.
7.  Filters and sorting work without extra AI calls.
8.  A student can save a favorite in the browser.
9.  The optional "remember my profile" choice works locally and can be
    cleared.
10. Application checklists are source-grounded and do not invent missing
    details.
11. Professor discovery, email drafting, and student-approved sending
    work if integrated without destabilizing the core flow; otherwise
    they are clearly identified as a limited/stretch module. No email is
    sent without explicit student approval of the final draft.
12. If live search or an AI provider fails, the app shows a graceful
    fallback or explicit error state.
13. No API secret is exposed in frontend code.
14. The app is deployed to the required Cloud of Alternate Futures
    platform, after platform capability checks.

## 10. Five-Hour Build Priorities

### P0 --- Must work

-   Verify deployment platform and create a trivial first deploy.
-   Landing page and profile form.
-   Curated fallback dataset.
-   Core search/retrieval path if feasible.
-   Structured opportunity cards with source links and verification
    labels.
-   Deterministic fit scoring and sorting.
-   Server-side secret handling.

### P1 --- Add after P0 is stable

-   Funding filters and tuition sorting.
-   Application checklists.
-   Local favorites and optional profile remembering.
-   AI-generated explanations grounded in validated fields.

### P2 --- Lightweight extension

-   Professor matching.
-   Personalized outreach email drafts and final-approval-gated sending.
-   Optional copy/mailto action.

If time or API access is limited, reduce live-source breadth and
professor-feature depth before compromising the core workflow, source
transparency, or deployment.

## 11. Risks and Mitigations

  -----------------------------------------------------------------------
  Risk                                Mitigation
  ----------------------------------- -----------------------------------
  Deployment platform lacks           Verify immediately; use a
  backend/runtime or outbound access  compatible architecture or curated
                                      demo mode.

  API credentials or quotas           Test at the beginning; keep
  unavailable                         deterministic scoring and curated
                                      fallback functional.

  Live source pages are unavailable   Bound timeouts; label unavailable
  or slow                             sources; return fallback data
                                      clearly.

  AI invents facts                    Structured extraction,
                                      source-linked evidence, validation,
                                      and uncertainty flags.

  False admission/eligibility         Use fit scores, not admission
  implications                        probabilities; distinguish likely
                                      matches from unknowns.

  Search returns too few results      Return fewer credible results
                                      rather than fabricate results.

  Five-hour scope overrun             Protect P0 requirements; defer
                                      optional modules.

  Student profile privacy risk        No accounts/database; opt-in
                                      browser storage; clear-profile
                                      action.

  Professor outreach is inappropriate Use official contact details only;
  or inaccurate                       require final student approval and
                                      make one bounded, auditable send.
  -----------------------------------------------------------------------

## 12. Open Decisions / Assumptions

-   Exact Cloud of Alternate Futures URL, runtime, outbound network
    access, and environment-variable support.
-   Which search provider and AI API credentials are available.
-   Whether visa/proof-of-funds information is included in the MVP or
    deferred.
-   Exact curated fallback dataset size and initial coverage.
-   Exact scoring weights; must be explicit, deterministic, and
    versioned.
-   Exact number of professor results.
-   Which transactional email provider and sender identity are available,
    and whether an optional mailto link can be implemented within the
    available time.
-   Actual performance and concurrency targets; the source document's
    proposed values are not yet adopted as hard SLAs.

## 13. Product Success Measures for the Demo

Measure what the demo can honestly demonstrate: - Time from profile
submission to first results. - Number of opportunities with official
source links. - Number of records with clear verification status. -
Number of opportunities compared in one search. - Whether a student can
understand why a result matched. - Whether fallback works when live
services fail. - Whether the student can save a favorite,
generate/edit an outreach draft, and approve it for sending.

Do not claim production-scale coverage, guaranteed eligibility, or
admission outcomes based only on a hackathon demonstration.
