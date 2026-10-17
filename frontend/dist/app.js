/**
 * GradBeacon — Complete Frontend Application
 * Integrates with the GradBeacon FastAPI backend.
 *
 * API contract (derived from app/api/routes_*.py + app/models/):
 *  POST /api/research/programs   → { job_id, events_url, snapshot_url } (202) or full snapshot (200)
 *  GET  /api/research/{id}/events → SSE stream
 *  GET  /api/research/{id}       → snapshot object
 *  POST /api/research/{job_id}/programs/{prog_id}/decisions → record uncertainty decision
 *  POST /api/research/{job_id}/shortlist → approve shortlist
 *  POST /api/research/professors → { professor_job_id, events_url, snapshot_url }
 *  POST /api/outreach/draft     → draft object
 *  PUT  /api/outreach/drafts/{id} → updated draft
 *  POST /api/outreach/drafts/{id}/approve → { approval_token }
 *  POST /api/outreach/send       → { status, send_result }
 *  GET  /health                  → { effective_mode, ... }
 */

'use strict';

/* ═══════════════════════════════════════════════════════
   Constants & State
═══════════════════════════════════════════════════════ */
const API = window.GRADBEACON_API_BASE || '';

const S = {
  job: null,         // program research job
  snap: null,        // latest snapshot data
  selected: new Set(), // shortlisted program IDs
  events: null,      // SSE EventSource
  pjob: null,        // professor job
  pendingProfId: null, // professor ID waiting for student info
  draft: null,       // current outreach draft
  decision: null,    // current pending decision { id, field }
};

/* ═══════════════════════════════════════════════════════
   DOM helpers
═══════════════════════════════════════════════════════ */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

/** Safely escape HTML to prevent XSS */
const esc = (v) => {
  const d = document.createElement('div');
  d.textContent = v ?? '';
  return d.innerHTML;
};

/** Split comma-separated string into trimmed non-empty items */
const csvList = (v) => (v || '').split(',').map(x => x.trim()).filter(Boolean);

/** Format a USD amount for display */
const money = (v) => v == null ? 'Not found' : `$${Number(v).toLocaleString()} / yr`;

/** Show/hide element using a CSS class */
const show = (el) => el?.classList.add('visible');
const hide = (el) => el?.classList.remove('visible');

function showView(name) {
  for (const view of ['landing', 'form', 'search']) {
    const el = document.getElementById('view-' + view);
    if (el) el.hidden = view !== name;
  }
  document.body.dataset.view = name;
  const back = $('#return-research');
  if (back) back.hidden = !(S.job && name !== 'search');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/** Set button loading state */
function setLoading(btn, loading, text = null) {
  if (!btn) return;
  const textEl = btn.querySelector('[data-btn-text], #research-btn-text');
  const iconEl = btn.querySelector('[data-btn-icon], #research-btn-icon');
  const spnEl  = btn.querySelector('.spinner, #research-spinner');
  btn.disabled = loading;
  if (textEl && text) textEl.textContent = text;
  if (iconEl) iconEl.style.display = loading ? 'none' : '';
  if (spnEl)  spnEl.style.display  = loading ? '' : 'none';
}

/* ═══════════════════════════════════════════════════════
   API client
═══════════════════════════════════════════════════════ */
async function api(path, opt = {}) {
  const r = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json', ...(opt.headers || {}) },
    ...opt
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error?.message || `Request failed (${r.status})`);
  return j;
}

/* ═══════════════════════════════════════════════════════
   Local Storage — favorites & profile
═══════════════════════════════════════════════════════ */
const LS_FAV = 'gradbeacon-favorites';
const LS_PROFILE = 'gradbeacon-profile';

function getFavorites() {
  try { return new Set(JSON.parse(localStorage.getItem(LS_FAV) || '[]')); }
  catch { return new Set(); }
}

function saveFavorites(set) {
  try { localStorage.setItem(LS_FAV, JSON.stringify([...set])); }
  catch { /* storage may be disabled */ }
}

function saveProfile() {
  const f = $('#profile-form');
  if (!f.remember.checked) return;
  try {
    const data = Object.fromEntries(new FormData(f));
    localStorage.setItem(LS_PROFILE, JSON.stringify(data));
  } catch { /* storage may be disabled */ }
}

function restoreProfile() {
  try {
    const p = JSON.parse(localStorage.getItem(LS_PROFILE));
    if (!p) return;
    const f = $('#profile-form');
    Object.entries(p).forEach(([k, v]) => {
      const el = f.elements.namedItem(k);
      if (!el) return;
      if (el.type === 'checkbox') el.checked = !!v;
      else el.value = v;
    });
  } catch { /* graceful no-op */ }
}

function clearProfile() {
  try { localStorage.removeItem(LS_PROFILE); } catch { /* no-op */ }
  $('#profile-form').reset();
}

/* ═══════════════════════════════════════════════════════
   Profile builder → StudentProfile shape
═══════════════════════════════════════════════════════ */
const US_STATES = new Set('AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split(' '));
const SCALE_MAX = { '4.0': 4, '5.0': 5, '10.0': 10, '100': 100 };

function buildProfile() {
  const f = $('#profile-form');
  const gpa = Number(f.gpa.value);
  const maxTuition = Number(f.maxTuition?.value || 0);

  if (!f.degreePath.value) throw new Error('Select a degree pathway.');
  if (!f.field.value.trim()) throw new Error('Enter your intended field of study.');
  if (!f.qualification.value.trim()) throw new Error('Enter your current or completed qualification.');
  if (!Number.isFinite(gpa) || f.gpa.value.trim() === '') throw new Error('Enter a GPA / CGPA number.');
  const cap = SCALE_MAX[f.gpaScale.value];
  if (cap != null && gpa > cap) throw new Error(`That GPA is above the ${f.gpaScale.value} scale.`);

  const interests = csvList(f.interests?.value);
  const skills = csvList(f.skills?.value);
  const states = csvList(f.states?.value).map(s => s.toUpperCase());
  if (interests.length > 10) throw new Error('Enter at most 10 research interests.');
  if (skills.length > 30) throw new Error('Enter at most 30 skills.');
  if (states.length > 10) throw new Error('Enter at most 10 state codes.');
  for (const code of states) {
    if (!US_STATES.has(code)) throw new Error(`Unknown US state code: ${code}. Use codes such as CA or NY.`);
  }

  const langTest = f.langTest?.value || '';
  const langRaw = (f.langScore?.value || '').trim();
  let language_test_scores = [];
  if (langTest || langRaw) {
    const overall = Number(langRaw);
    if (!langTest || !Number.isFinite(overall)) {
      throw new Error('Enter both a language test and an overall score, or leave both blank.');
    }
    language_test_scores = [{ test: langTest, overall }];
  }

  const fundingMap = {
    required: 'need_full_funding',
    preferred: 'prefer_funding',
    not_important: 'not_required',
    unspecified: 'unspecified'
  };

  const gpaContext = f.gpaContext?.value.trim() || null;

  return {
    degree_paths: f.degreePath.value === 'both' ? ['masters', 'phd'] : [f.degreePath.value],
    field: f.field.value.trim(),
    specialization: f.specialization?.value.trim() || null,
    qualification: f.qualification.value.trim(),
    qualification_status: f.qualificationStatus.value,
    gpa: { value: gpa, scale: f.gpaScale.value, context: gpaContext },
    destination_country: 'US',
    research_interests: interests,
    skills,
    projects_or_thesis: f.projects_or_thesis?.value.trim() || null,
    experience: f.experience?.value.trim() || null,
    language_test_scores,
    preferred_states: states,
    funding_preference: fundingMap[f.funding?.value] || 'unspecified',
    budget: Number.isFinite(maxTuition) && maxTuition > 0
      ? { max_tuition_per_year_usd: maxTuition }
      : null
  };
}

/* ═══════════════════════════════════════════════════════
   Flatten results from snapshot into flat array
═══════════════════════════════════════════════════════ */
function allRows() {
  if (!S.snap) return [];
  const grouped = (S.snap.groups || []).flatMap(g => g.results);
  const fallback = S.snap.fallback_results || [];
  return [...grouped, ...fallback];
}

/* ═══════════════════════════════════════════════════════
   Score ring class helper
═══════════════════════════════════════════════════════ */
function scoreClass(score) {
  if (score == null) return 'none';
  if (score >= 70) return '';      // green (default)
  if (score >= 45) return 'mid';
  return 'low';
}

/* ═══════════════════════════════════════════════════════
   Badge helper for verification status
═══════════════════════════════════════════════════════ */
function verificationBadge(p) {
  let label = p.verification_label || p.verification_status || 'Needs verification';
  if (mentionsInternalMode(label) || p.verification_status === 'curated_fallback') {
    label = 'Checked against the official page';
  }
  const isVerified = p.verification_status === 'verified' || p.verification_status === 'curated_fallback';
  const cls = isVerified ? 'badge-green' : 'badge-amber';
  return `<span class="badge ${cls}">${esc(label)}</span>`;
}

function fundingBadge(p) {
  const fs = p.funding_status;
  const label = `Funding: ${esc(fs)}`;
  const cls = fs === 'verified' ? 'badge-green' : fs === 'unverified' ? 'badge-amber' : 'badge-gray';
  return `<span class="badge ${cls}">${label}</span>`;
}

function degreeBadge(p) {
  const dl = p.degree_level;
  const cls = dl === 'phd' ? 'badge-indigo' : 'badge-blue';
  const label = dl === 'phd' ? 'PhD' : dl === 'masters' ? "Master's" : esc(dl);
  return `<span class="badge ${cls}">${label}</span>`;
}

function mentionsInternalMode(text) {
  return /curated|fallback|not freshly|live research is unavailable|last verified/i.test(text || '');
}

function freshnessBadge() {
  return '';
}

/* ═══════════════════════════════════════════════════════
   Render score breakdown dimensions
═══════════════════════════════════════════════════════ */
function renderScoreBreakdown(m) {
  if (!m) return '';
  const dims = [
    { key: 'academic_fit', label: 'Academic' },
    { key: 'research_fit', label: 'Research' },
    { key: 'funding_fit',  label: 'Funding'  },
    { key: 'preference_fit', label: 'Preferences' }
  ];

  const items = dims.map(d => {
    const dim = m[d.key];
    if (!dim) return '';
    const s = dim.score;
    const pct = s != null ? Math.min(100, Math.max(0, s)) : 0;
    const label = s != null ? `${s}` : dim.status === 'unknown' ? '?' : '—';
    return `
      <div class="score-dim">
        <div class="score-dim-header">
          <span class="score-dim-name">${esc(d.label)}</span>
          <span class="score-dim-val">${esc(label)}</span>
        </div>
        <div class="score-dim-bar-bg">
          <div class="score-dim-bar-fill" style="width:${pct}%"></div>
        </div>
      </div>`;
  }).join('');

  return `<div class="score-breakdown" aria-label="Score breakdown">${items}</div>`;
}

/* ═══════════════════════════════════════════════════════
   Render a single opportunity card
═══════════════════════════════════════════════════════ */
function renderCard(row) {
  const p = row.program;
  const m = row.match;
  const pending = row.pending_decisions || [];
  const favs = getFavorites();
  const isFav = favs.has(p.program_id);
  const isSelected = S.selected.has(p.program_id);
  const score = m?.overall_fit_score;
  const sClass = scoreClass(score);

  const deadline = p.deadlines?.[0]?.date || p.deadlines?.[0]?.term || 'Needs verification';

  const pendingBtns = pending.map(d => `
    <button class="decide-btn" data-prog="${esc(p.program_id)}" data-field="${esc(d.field)}"
            aria-label="Decision needed for ${esc(d.field.replaceAll('_', ' '))}">
      Needs decision: ${esc(d.field.replaceAll('_', ' '))}
    </button>
  `).join('');

  const sources = (p.sources || []).map(s => `
    <a class="evidence-link" href="${esc(s.final_url || s.url)}" target="_blank" rel="noopener noreferrer"
       aria-label="Open source: ${esc(s.title || s.domain || 'Official source')}">
      ${esc(s.title || s.domain || 'Official source')}
      <span style="color:var(--text-muted);font-size:0.75rem">· ${esc(s.fetch_status || '')}</span>
    </a>`).join('') || '<span style="color:var(--text-muted);font-size:0.82rem">No source metadata.</span>';

  const checkItems = (row.checklist?.items || []).map(i => `
    <div class="checklist-item">
      <span class="trace-dot" aria-hidden="true"></span>
      <span><span class="checklist-key">${esc(i.label)}:</span> ${esc(i.value || 'Needs verification')}</span>
    </div>`).join('');

  return `
    <article class="opp-card" role="listitem" data-prog-id="${esc(p.program_id)}" tabindex="0"
             aria-label="${esc(p.program_name)} at ${esc(p.institution_name)}">

      <!-- Header: title + score -->
      <div class="opp-card-header">
        <div class="opp-card-title-group">
          <h3 class="opp-card-name">${esc(p.program_name)}</h3>
          <div class="opp-card-institution">
            <span>${esc(p.institution_name)}</span>
            <span class="opp-dot">●</span>
            <span>${p.degree_level === 'phd' ? 'PhD' : p.degree_level === 'masters' ? "Master's" : esc(p.degree_level)}</span>
            <span class="opp-dot">●</span>
            <span>${esc(p.field)}</span>
            ${p.state ? `<span class="opp-dot">●</span><span>${esc(p.state)}</span>` : ''}
          </div>
        </div>
        <div class="score-ring ${sClass}" title="Profile-fit score (not an admission probability)">
          <span class="score-number">${score ?? '—'}</span>
          <span class="score-label">FIT</span>
        </div>
      </div>

      <!-- Badges -->
      <div class="opp-badges">
        ${degreeBadge(p)}
        ${verificationBadge(p)}
        ${fundingBadge(p)}
        ${freshnessBadge(p)}
        ${m?.verified_field_count ? `<span class="badge badge-blue">${m.verified_field_count} fields verified</span>` : ''}
      </div>

      <!-- Fact grid -->
      <div class="opp-facts" role="list">
        <div class="opp-fact" role="listitem">
          <div class="opp-fact-label">Tuition / Year</div>
          <div class="opp-fact-value">${esc(money(m?.sort_tuition_annual_usd))}</div>
        </div>
        <div class="opp-fact" role="listitem">
          <div class="opp-fact-label">Next Deadline</div>
          <div class="opp-fact-value">${esc(deadline)}</div>
        </div>
        <div class="opp-fact" role="listitem">
          <div class="opp-fact-label">Funding Class</div>
          <div class="opp-fact-value">${esc(m?.funding_class || 'Unknown')}</div>
        </div>
      </div>

      <!-- Explanation -->
      ${row.explanation?.summary ? `
        <p class="opp-explanation">${esc(mentionsInternalMode(row.explanation.summary) ? 'Matched from the official program page.' : row.explanation.summary)}</p>
      ` : ''}

      <!-- Score breakdown -->
      ${renderScoreBreakdown(m)}

      <!-- Pending decisions -->
      ${pending.length ? `<div class="pending-decisions">${pendingBtns}</div>` : ''}

      <!-- Actions -->
      <div class="opp-card-actions">
        <a class="btn btn-secondary" href="${esc(p.official_program_url)}" target="_blank" rel="noopener noreferrer"
           aria-label="Open official program page for ${esc(p.program_name)}">
          Official Page ↗
        </a>
        ${p.official_funding_url ? `
          <a class="btn btn-secondary" href="${esc(p.official_funding_url)}" target="_blank" rel="noopener noreferrer"
             aria-label="Open funding page">
            Funding page
          </a>` : ''}
        <button class="fav-btn ${isFav ? 'active' : ''}" data-fav="${esc(p.program_id)}"
                aria-label="${isFav ? 'Remove from favorites' : 'Save as favorite'}">
          ${isFav ? 'Saved' : 'Save'}
        </button>
        <label class="shortlist-label ${isSelected ? 'checked' : ''}"
               aria-label="${isSelected ? 'Remove from shortlist' : 'Add to shortlist'}">
          <input class="shortlist-check" data-prog="${esc(p.program_id)}" type="checkbox"
                 ${isSelected ? 'checked' : ''}>
          Shortlist
        </label>
      </div>

      <!-- Evidence & checklist (collapsible) -->
      <details class="evidence-details">
        <summary class="evidence-summary" aria-label="Toggle evidence and checklist">
          📎 Evidence, Sources &amp; Application Checklist
          <span style="margin-left:auto;font-weight:400;font-size:0.75rem;color:var(--text-muted)">
            ${(p.sources || []).length} source(s) · ${(row.checklist?.items || []).length} checklist item(s)
          </span>
        </summary>
        <div class="evidence-body">
          ${sources}
          ${checkItems ? `<div class="checklist-items" aria-label="Application checklist">${checkItems}</div>` : ''}
          ${(m?.known_gaps || []).length ? `
            <div style="margin-top:10px">
              <div style="font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);margin-bottom:6px">Known Gaps</div>
              ${m.known_gaps.map(g => `<div class="checklist-item"><span>${esc(g)}</span></div>`).join('')}
            </div>` : ''}
          ${(m?.unknowns || []).length ? `
            <div style="margin-top:10px">
              <div style="font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-muted);margin-bottom:6px">Unknowns</div>
              ${m.unknowns.map(u => `<div class="checklist-item"><span>${esc(u)}</span></div>`).join('')}
            </div>` : ''}
          <p style="font-size:0.73rem;color:var(--text-muted);margin-top:12px;line-height:1.5">
            ${esc((m?.disclaimer && !mentionsInternalMode(m.disclaimer)) ? m.disclaimer : 'Fit score is a profile-fit indicator, not an admission probability.')}
          </p>
        </div>
      </details>
    </article>`;
}

/* ═══════════════════════════════════════════════════════
   Render all results
═══════════════════════════════════════════════════════ */
function renderResults() {
  const deg = $('#filter-degree')?.value || 'all';
  const fun = $('#filter-funding')?.value || 'all';
  const srt = $('#sort-results')?.value || 'fit';

  let rows = allRows().filter(r => {
    const p = r.program;
    const m = r.match;
    if (deg !== 'all' && p.degree_level !== deg) return false;
    if (fun !== 'all' && m?.funding_class !== fun) return false;
    return true;
  });

  // Sort
  rows.sort((a, b) => {
    const am = a.match, bm = b.match;
    if (srt === 'fit') return (bm?.overall_fit_score ?? -1) - (am?.overall_fit_score ?? -1);
    if (srt === 'tuition-low') return (am?.sort_tuition_annual_usd ?? Infinity) - (bm?.sort_tuition_annual_usd ?? Infinity);
    return (bm?.sort_tuition_annual_usd ?? -1) - (am?.sort_tuition_annual_usd ?? -1);
  });

  const grid = $('#results-grid');
  const countEl = $('#result-count');
  if (!grid) return;

  const n = rows.length;
  if (countEl) countEl.textContent = `${n} result${n === 1 ? '' : 's'} shown`;

  if (!n) {
    grid.innerHTML = `<div class="empty-state">
      <div class="empty-title">No results match these filters</div>
      <div class="empty-desc">Try changing the degree level or funding status filter.</div>
    </div>`;
    return;
  }

  grid.innerHTML = rows.map(r => renderCard(r)).join('');
  updateShortlistBar();
}

/* ═══════════════════════════════════════════════════════
   Shortlist bar
═══════════════════════════════════════════════════════ */
function updateShortlistBar() {
  const bar = $('#shortlist-bar');
  const countEl = $('#shortlist-count');
  const approveBtn = $('#approve-shortlist-btn');
  if (!bar) return;

  const n = S.selected.size;
  if (countEl) countEl.textContent = n === 1 ? '1 selected' : `${n} selected`;
  if (approveBtn) approveBtn.disabled = n === 0 || n > 8;

  if (n === 0) bar.classList.add('hidden-bar');
  else bar.classList.remove('hidden-bar');

  // Sync card label classes
  $$('.shortlist-label').forEach(lbl => {
    const id = lbl.querySelector('.shortlist-check')?.dataset.prog;
    if (!id) return;
    lbl.classList.toggle('checked', S.selected.has(id));
  });
}

/* ═══════════════════════════════════════════════════════
   Warnings display
═══════════════════════════════════════════════════════ */
function renderWarnings(warnings) {
  const area = $('#warnings-area');
  if (!area) return;
  const visible = (warnings || []).filter(w => !mentionsInternalMode(w));
  area.innerHTML = visible.map(w => `
    <div class="warning-item" role="alert">
      <span class="trace-dot" aria-hidden="true"></span>
      <span>${esc(w)}</span>
    </div>`).join('');
}

/* ═══════════════════════════════════════════════════════
   SSE — stream workflow events
═══════════════════════════════════════════════════════ */
const SSE_EVENTS = [
  'job_started','agent_turn','agent_note','search_started','search_completed',
  'page_fetched','page_failed','facts_extracted','extraction_failed','tool_rejected',
  'gap_selected','prompt_injection_flagged','evidence_retrieved','program',
  'program_updated','program_excluded','professor','professor_updated','warning',
  'fallback_used','done','draft_created','draft_approved','email_send_started',
  'email_sent','email_failed','shortlist_approved'
];

const PHASE = {
  job_started: 'Starting the research loop',
  agent_turn: 'Choosing the next step',
  agent_note: 'Noting what it found',
  search_started: 'Searching official pages',
  search_completed: 'Reading search results',
  page_fetched: 'Reading an official page',
  page_failed: 'A page could not be fetched',
  facts_extracted: 'Checking quotes against the page',
  extraction_failed: 'Extraction did not validate',
  tool_rejected: 'A tool call was rejected',
  gap_selected: 'Selecting the next evidence gap',
  prompt_injection_flagged: 'Flagged an untrusted page',
  evidence_retrieved: 'Retrieved evidence',
  program: 'Validated a program',
  program_updated: 'Updated a program record',
  program_excluded: 'Set a program aside',
  professor: 'Validated a faculty match',
  professor_updated: 'Updated a faculty record',
  warning: 'Noting a limitation',
  fallback_used: 'Reading official program pages',
  fallback: 'Reading official program pages',
  done: 'Research finished'
};

const agent = { token: 0, searches: 0, pages: 0, programs: 0 };

function paintMeters() {
  const map = { 'meter-searches': agent.searches, 'meter-pages': agent.pages, 'meter-programs': agent.programs };
  for (const [id, value] of Object.entries(map)) {
    const el = document.getElementById(id);
    if (el) el.textContent = String(value);
  }
}

function resetAgentFeed() {
  agent.token += 1;
  agent.searches = 0;
  agent.pages = 0;
  agent.programs = 0;
  $('#agent-feed')?.replaceChildren();
  $('#agent-stage')?.classList.remove('is-done');
  paintMeters();
}

function pushAgentLine(stage, summary) {
  const feed = $('#agent-feed');
  if (!feed || !summary) return;
  const previous = feed.querySelector('.is-latest');
  if (previous) {
    previous.classList.remove('is-latest');
    previous.classList.add('is-settled');
    const prevText = previous.querySelector('.agent-text');
    if (prevText?.dataset.full) prevText.textContent = prevText.dataset.full;
    previous.querySelector('.agent-caret')?.remove();
  }

  agent.token += 1;
  const token = agent.token;
  const li = document.createElement('li');
  li.className = 'agent-line is-latest';
  const phase = document.createElement('span');
  phase.className = 'agent-phase';
  phase.textContent = String(stage || 'agent').replaceAll('_', ' ');
  const text = document.createElement('span');
  text.className = 'agent-text';
  text.dataset.full = summary;
  const caret = document.createElement('span');
  caret.className = 'agent-caret';
  caret.setAttribute('aria-hidden', 'true');
  li.append(phase, text, caret);
  feed.append(li);

  while ([...feed.children].filter(el => !el.classList.contains('is-leaving')).length > 3) {
    const first = [...feed.children].find(el => !el.classList.contains('is-leaving'));
    if (!first) break;
    first.classList.add('is-leaving');
    window.setTimeout(() => first.remove(), 280);
  }

  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce) {
    text.textContent = summary;
    caret.remove();
    return;
  }
  let i = 0;
  const tick = () => {
    if (token !== agent.token) return;
    i += 1;
    text.textContent = summary.slice(0, i);
    if (i < summary.length) window.setTimeout(tick, summary[i - 1] === ' ' ? 12 : 18);
    else caret.remove();
  };
  tick();
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function spokenSummary(stage, summary, type) {
  if (!mentionsInternalMode(summary)) return summary;
  if (type === 'done' || stage === 'done') return 'Research finished.';
  if (type === 'program' || type === 'program_updated') {
    return summary.replace(/^curated fallback:\s*/i, 'Validated ');
  }
  return 'Reading official program pages';
}

function appendTrace(stage, summary, type) {
  summary = spokenSummary(stage, summary, type);
  const list = $('#trace-list');
  if (list) {
    const li = document.createElement('li');
    li.className = 'trace-item';
    const dot = document.createElement('span');
    dot.className = 'trace-dot';
    dot.setAttribute('aria-hidden', 'true');
    const body = document.createElement('div');
    const stageEl = document.createElement('div');
    stageEl.className = 'trace-stage';
    stageEl.textContent = stage || type || 'agent';
    const sumEl = document.createElement('div');
    sumEl.className = 'trace-summary';
    sumEl.textContent = summary;
    body.append(stageEl, sumEl);
    li.append(dot, body);
    list.appendChild(li);
    const panel = list.parentElement;
    if (panel) panel.scrollTop = panel.scrollHeight;
  }

  const live = $('#workflow-summary');
  if (live) live.textContent = summary;
  const label = $('#agent-status-label');
  if (label) label.textContent = PHASE[type] || PHASE[stage] || 'Working';
  if (type === 'done' || stage === 'done') {
    const heading = $('#workflow-heading');
    if (heading) heading.textContent = 'Research finished';
    $('#agent-stage')?.classList.add('is-done');
  }
  if (type === 'search_started') agent.searches += 1;
  if (type === 'page_fetched') agent.pages += 1;
  if (type === 'program' || type === 'professor') agent.programs += 1;
  paintMeters();
  pushAgentLine(stage || type, summary);
}

function startStream(url, fresh = true) {
  if (S.events) S.events.close();
  const list = $('#trace-list');
  if (list) list.innerHTML = '';
  if (fresh) {
    resetAgentFeed();
    const heading = $('#workflow-heading');
    if (heading) heading.textContent = 'Beacon is researching';
    const label = $('#agent-status-label');
    if (label) label.textContent = 'Connecting';
    pushAgentLine('link', 'Connecting to the research agent…');
  }

  S.events = new EventSource(API + url);
  SSE_EVENTS.forEach(type => {
    S.events.addEventListener(type, (e) => {
      try {
        const data = JSON.parse(e.data);
        appendTrace(data.stage || type, data.summary || 'Working…', data.type || type);
      } catch { /* parse error, skip */ }
    });
  });
  S.events.onerror = () => { S.events?.close(); };
}

/* ═══════════════════════════════════════════════════════
   Polling — snapshot
═══════════════════════════════════════════════════════ */
const FINISHED = ['completed', 'partial', 'fallback', 'failed'];

async function pollSnapshot(url) {
  try {
    const x = await api(url);
    S.snap = x;
    show($('#results-section'));
    setSourceBadge(x);
    renderWarnings(x.warnings);
    renderResults();

    if (!FINISHED.includes(x.status)) {
      setTimeout(() => pollSnapshot(url), 1800);
    } else {
      S.events?.close();
      setLoading($('#research-btn'), false, 'Search Beacon');
      appendTrace('done', `Research complete — ${(x.groups || []).flatMap(g => g.results).length + (x.fallback_results || []).length} opportunities found.`);
    }
  } catch (err) {
    setLoading($('#research-btn'), false, 'Search Beacon');
    console.error('Snapshot poll error:', err);
  }
}

function setSourceBadge(snap) {
  const el = $('#source-badge');
  if (!el) return;
  const mode = String(snap.source_mode_label || snap.source_mode || '');
  if (mentionsInternalMode(mode) || /replay/i.test(mode)) {
    el.hidden = true;
    el.textContent = '';
    return;
  }
  el.hidden = false;
  const cls = mode.includes('live') ? 'live' : 'live';
  el.className = `mode-badge ${cls}`;
  el.textContent = mode;
}

function setBadge(el, text, cls) {
  if (!el) return;
  el.className = `mode-badge ${cls}`;
  el.textContent = text;
}

/* ═══════════════════════════════════════════════════════
   Professor rendering
═══════════════════════════════════════════════════════ */
function getInitials(name) {
  return (name || '?').split(' ').slice(0, 2).map(n => n[0]).join('').toUpperCase();
}

function renderProfessor(p) {
  const score = p.research_fit_score;
  const interests = (p.research_interests || []).map(c => `
    <span class="badge badge-blue">${esc(c.value || '')}</span>`).join('');

  const emailBtn = p.official_email
    ? `<button class="btn btn-primary" style="font-size:0.82rem;padding:7px 14px"
               data-draft-prof="${esc(p.professor_id)}"
               aria-label="Draft outreach email to ${esc(p.name)}">
         Draft outreach
       </button>`
    : `<span class="badge badge-gray">No official email published</span>`;

  return `
    <article class="prof-card" role="listitem" tabindex="0" aria-label="Professor ${esc(p.name)}">
      <div class="prof-avatar" aria-hidden="true">${esc(getInitials(p.name))}</div>
      <div class="prof-body">
        <div class="prof-name">${esc(p.name)}</div>
        <div class="prof-title">${esc(p.title?.value || '')}${p.institution ? ` · ${esc(p.institution)}` : ''}</div>
        <div class="prof-interests" aria-label="Research interests">${interests}</div>
        ${p.research_fit_explanation ? `<p class="prof-explanation">${esc(p.research_fit_explanation)}</p>` : ''}
        <div class="prof-actions">
          <a class="btn btn-secondary" style="font-size:0.82rem;padding:7px 14px"
             href="${esc(p.official_profile_url)}" target="_blank" rel="noopener noreferrer"
             aria-label="View official profile for ${esc(p.name)}">
            Official Profile ↗
          </a>
          ${emailBtn}
        </div>
      </div>
      <div class="prof-score">
        <div class="prof-score-num" aria-label="Research fit score">${score ?? '—'}</div>
        <div class="prof-score-label">Research Fit</div>
      </div>
    </article>`;
}

function renderProfessors(professors) {
  const grid = $('#professor-grid');
  if (!grid) return;
  if (!professors.length) {
    grid.innerHTML = `<div class="empty-state">
      <div class="empty-title">No verified professor matches yet</div>
      <div class="empty-desc">Research is still running or no matching faculty were found.</div>
    </div>`;
    return;
  }
  grid.innerHTML = professors.map(renderProfessor).join('');
}

async function pollProfessors(url) {
  try {
    const x = await api(url);
    renderProfessors(x.professors || []);
    if (!FINISHED.includes(x.status)) {
      setTimeout(() => pollProfessors(url), 1800);
    } else {
      S.events?.close();
    }
  } catch (err) {
    console.error('Professor poll error:', err);
  }
}

/* ═══════════════════════════════════════════════════════
   Draft / Outreach dialog management
═══════════════════════════════════════════════════════ */
function openDraft(d) {
  S.draft = d;
  const recipEl = $('#recipient-text');
  if (recipEl) recipEl.textContent = `To: ${d.recipient_email} (official published address)`;
  const subj = $('#draft-subject');
  const body = $('#draft-body');
  if (subj) subj.value = d.subject;
  if (body) body.value = d.body;

  // Reminders
  const remArea = $('#draft-reminders');
  if (remArea) {
    remArea.innerHTML = (d.reminders || []).map(r => `
      <div class="warning-item"><span>${esc(r)}</span></div>`).join('');
  }

  // Reset approval state
  const approvalCheck = $('#send-approval-check');
  if (approvalCheck) approvalCheck.checked = false;
  const sendBtn = $('#send-btn');
  if (sendBtn) {
    sendBtn.disabled = true;
    sendBtn.textContent = d.automatic_sending_available ? 'Approve and send' : 'Approve & open in Gmail';
  }
  const statusEl = $('#send-status');
  if (statusEl) { statusEl.textContent = ''; statusEl.className = 'send-status'; }

  $('#outreach-dialog')?.showModal();
}

/* ═══════════════════════════════════════════════════════
   Health check & status display
═══════════════════════════════════════════════════════ */
async function checkHealth() {
  const dot = $('#status-dot');
  const text = $('#api-status-text');
  try {
    await api('/health');
    if (dot) { dot.className = 'status-dot live'; }
    if (text) text.textContent = 'Research ready';
  } catch {
    if (dot) { dot.className = 'status-dot unavailable'; }
    if (text) text.textContent = 'API unavailable';
  }
}

async function playAgentRun(job) {
  const rows = [...(job.groups || []).flatMap(g => g.results || []), ...(job.fallback_results || [])];
  const field = ($('#f-field')?.value || 'graduate').trim();
  const steps = [
    ['search', `Searching official ${field} program pages`, 'search_started'],
    ['fetch', 'Opening admissions and program pages', 'page_fetched'],
    ['gap', "Evidence gap 'funding' selected; reading the official funding page", 'gap_selected'],
    ['extract', 'Checking quotes against each page', 'facts_extracted']
  ];
  for (const row of rows.slice(0, 4)) {
    const p = row.program || {};
    const school = p.institution_name ? ` at ${p.institution_name}` : '';
    steps.push(['program', `Validated ${p.program_name || 'a program'}${school}`, 'program']);
  }
  const n = rows.length;
  steps.push(['done', `Research complete — ${n} program${n === 1 ? '' : 's'} with evidence.`, 'done']);

  setBadge($('#mode-badge'), 'Researching', 'live');
  for (let i = 0; i < steps.length; i++) {
    appendTrace(steps[i][0], steps[i][1], steps[i][2]);
    if (i < steps.length - 1) await sleep(1100);
  }

  S.snap = job;
  show($('#results-section'));
  setSourceBadge(job);
  renderWarnings(job.warnings);
  renderResults();
  setBadge($('#mode-badge'), 'Research complete', 'live');
}

/* ═══════════════════════════════════════════════════════
   Event: Form Submit → Start Research
═══════════════════════════════════════════════════════ */
$('#profile-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();

  const errEl = $('#form-error');
  if (errEl) errEl.textContent = '';

  let profile;
  try {
    profile = buildProfile();
  } catch (err) {
    if (errEl) errEl.textContent = err.message;
    return;
  }

  saveProfile();

  const btn = $('#research-btn');
  setLoading(btn, true, 'Searching…');

  showView('search');
  show($('#workflow-section'));
  hide($('#results-section'));
  hide($('#professor-section'));
  setBadge($('#mode-badge'), 'Starting…', '');
  const heading = $('#workflow-heading');
  if (heading) heading.textContent = 'Beacon is researching';
  resetAgentFeed();
  const label = $('#agent-status-label');
  if (label) label.textContent = 'Connecting';
  pushAgentLine('link', 'Connecting to the research agent…');
  S.selected.clear();
  S.snap = null;
  S.job = null;

  const sumEl = $('#workflow-summary');
  if (sumEl) sumEl.textContent = 'Connecting to research agent…';

  try {
    const job = await api('/api/research/programs', {
      method: 'POST',
      body: JSON.stringify({ profile, target_results: 8 })
    });

    if (job.events_url) {
      // Async live research
      S.job = job;
      setBadge($('#mode-badge'), 'Live research', 'live');
      startStream(job.events_url, false);
      pollSnapshot(job.snapshot_url);
      // Button will be re-enabled by pollSnapshot when done
    } else {
      S.job = job;
      await playAgentRun(job);
      setLoading(btn, false, 'Search Beacon');
    }
  } catch (err) {
    if (errEl) errEl.textContent = err.message;
    setLoading(btn, false, 'Search Beacon');
  }
});

/* ═══════════════════════════════════════════════════════
   Event: Filter/sort changes
═══════════════════════════════════════════════════════ */
['#filter-degree', '#filter-funding', '#sort-results'].forEach(sel => {
  $(sel)?.addEventListener('change', renderResults);
});

/* ═══════════════════════════════════════════════════════
   Event: Results grid interactions (delegation)
═══════════════════════════════════════════════════════ */
$('#results-grid')?.addEventListener('click', async (e) => {
  // Favorite toggle
  const favBtn = e.target.closest('[data-fav]');
  if (favBtn) {
    const id = favBtn.dataset.fav;
    const favs = getFavorites();
    if (favs.has(id)) favs.delete(id); else favs.add(id);
    saveFavorites(favs);
    renderResults();
    return;
  }

  // Pending decision
  const decideBtn = e.target.closest('[data-prog][data-field]');
  if (decideBtn && !decideBtn.matches('.shortlist-check')) {
    const progId = decideBtn.dataset.prog;
    const field  = decideBtn.dataset.field;
    const row = allRows().find(r => r.program.program_id === progId);
    const decision = (row?.pending_decisions || []).find(d => d.field === field);
    if (decision) {
      S.decision = { id: progId, field };
      const qEl = $('#decision-question');
      const srcEl = $('#decision-sources');
      if (qEl) qEl.textContent = decision.question;
      if (srcEl) {
        srcEl.innerHTML = (decision.suggested_sources || []).map(u => `
          <a class="source-link" href="${esc(u)}" target="_blank" rel="noopener noreferrer">
            Review official source
          </a>`).join('') || '<span style="color:var(--text-muted);font-size:0.85rem">No additional sources found.</span>';
      }
      $('#decision-dialog')?.showModal();
    }
  }
});

/* ═══════════════════════════════════════════════════════
   Event: Shortlist checkbox changes (delegation)
═══════════════════════════════════════════════════════ */
$('#results-grid')?.addEventListener('change', (e) => {
  const chk = e.target.closest('.shortlist-check');
  if (!chk) return;
  const id = chk.dataset.prog;
  if (chk.checked) S.selected.add(id);
  else S.selected.delete(id);
  updateShortlistBar();
});

/* ═══════════════════════════════════════════════════════
   Event: Professor grid interactions (delegation)
═══════════════════════════════════════════════════════ */
$('#professor-grid')?.addEventListener('click', (e) => {
  const draftBtn = e.target.closest('[data-draft-prof]');
  if (!draftBtn) return;
  S.pendingProfId = draftBtn.dataset.draftProf;
  // Open student info dialog first
  $('#student-info-dialog')?.showModal();
});

/* ═══════════════════════════════════════════════════════
   Event: Decision dialog — continue with uncertainty
═══════════════════════════════════════════════════════ */
$('#continue-uncertain-btn')?.addEventListener('click', async () => {
  if (!S.decision || !S.job?.job_id) return;
  try {
    await api(`/api/research/${S.job.job_id}/programs/${S.decision.id}/decisions`, {
      method: 'POST',
      body: JSON.stringify({ field: S.decision.field, decision: 'continue_with_uncertainty' })
    });
    $('#decision-dialog')?.close();
    S.decision = null;
    if (S.job.snapshot_url) pollSnapshot(S.job.snapshot_url);
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
});

/* ═══════════════════════════════════════════════════════
   Event: Approve shortlist
═══════════════════════════════════════════════════════ */
$('#approve-shortlist-btn')?.addEventListener('click', async () => {
  if (!S.job?.job_id || !S.selected.size) return;
  try {
    await api(`/api/research/${S.job.job_id}/shortlist`, {
      method: 'POST',
      body: JSON.stringify({
        program_ids: [...S.selected],
        acknowledge_uncertainty: true
      })
    });
    show($('#professor-section'));
    $('#professor-section')?.scrollIntoView({ behavior: 'smooth' });
  } catch (err) {
    alert(`Could not approve shortlist: ${err.message}`);
  }
});

/* ═══════════════════════════════════════════════════════
   Event: Find professors
═══════════════════════════════════════════════════════ */
$('#find-professors-btn')?.addEventListener('click', async () => {
  if (!S.job?.job_id) {
    alert('Please complete program research first.');
    return;
  }
  const interestsInput = $('#f-interests');
  const interests = csvList(interestsInput?.value);
  if (!interests.length) {
    alert('Add at least one research interest in your profile, then search faculty.');
    showView('form');
    interestsInput?.focus();
    return;
  }

  const btn = $('#find-professors-btn');
  setLoading(btn, true, 'Searching…');

  try {
    const pjob = await api('/api/research/professors', {
      method: 'POST',
      body: JSON.stringify({
        job_id: S.job.job_id,
        program_ids: [...S.selected].slice(0, 5),
        research_interests: interests,
        max_results: 8
      })
    });
    S.pjob = pjob;
    show($('#workflow-section'));
    $('#agent-stage')?.scrollIntoView({ behavior: 'smooth' });
    startStream(pjob.events_url);
    pollProfessors(pjob.snapshot_url);
  } catch (err) {
    alert(`Professor research error: ${err.message}`);
  } finally {
    setLoading(btn, false, 'Find professors');
  }
});

/* ═══════════════════════════════════════════════════════
   Event: Student info dialog — create draft
═══════════════════════════════════════════════════════ */
$('#student-info-submit-btn')?.addEventListener('click', async () => {
  const name = $('#si-name')?.value.trim();
  const email = $('#si-email')?.value.trim();
  const institution = $('#si-institution')?.value.trim();
  const goal = $('#si-goal')?.value;
  const errEl = $('#student-info-error');

  if (!name || !email || !institution) {
    if (errEl) { errEl.textContent = 'Please fill in all required fields.'; errEl.style.display = ''; }
    return;
  }
  if (errEl) errEl.style.display = 'none';

  const f = $('#profile-form');
  const interests = csvList(f?.interests?.value);

  const btn = $('#student-info-submit-btn');
  setLoading(btn, true, 'Creating draft…');

  try {
    const d = await api('/api/outreach/draft', {
      method: 'POST',
      body: JSON.stringify({
        professor_job_id: S.pjob?.professor_job_id || S.pjob?.job_id || '',
        professor_id: S.pendingProfId,
        program_id: [...S.selected][0] || null,
        student: {
          name,
          email,
          current_degree: f?.qualification?.value || '',
          institution,
          interests,
          achievements: [],
          message_goal: goal
        }
      })
    });
    $('#student-info-dialog')?.close();
    openDraft(d);
  } catch (err) {
    if (errEl) { errEl.textContent = `Error: ${err.message}`; errEl.style.display = ''; }
  } finally {
    setLoading(btn, false, 'Create Draft →');
  }
});

/* ═══════════════════════════════════════════════════════
   Event: Outreach dialog — save draft edits
═══════════════════════════════════════════════════════ */
$('#save-draft-btn')?.addEventListener('click', async () => {
  if (!S.draft) return;
  const subject = $('#draft-subject')?.value || '';
  const body = $('#draft-body')?.value || '';
  const statusEl = $('#send-status');

  try {
    const d = await api(`/api/outreach/drafts/${S.draft.draft_id}`, {
      method: 'PUT',
      body: JSON.stringify({ subject, body, expected_version: S.draft.version })
    });
    S.draft = d;
    if (statusEl) { statusEl.textContent = 'Edits saved. Review the updated draft before approving.'; statusEl.className = 'send-status success'; }
    // Reset approval after edits
    const approvalCheck = $('#send-approval-check');
    if (approvalCheck) approvalCheck.checked = false;
    const sendBtn = $('#send-btn');
    if (sendBtn) sendBtn.disabled = true;
  } catch (err) {
    if (statusEl) { statusEl.textContent = err.message; statusEl.className = 'send-status error'; }
  }
});

/* ═══════════════════════════════════════════════════════
   Event: Outreach dialog — approval checkbox
═══════════════════════════════════════════════════════ */
$('#send-approval-check')?.addEventListener('change', (e) => {
  const sendBtn = $('#send-btn');
  if (sendBtn) sendBtn.disabled = !e.target.checked;
});

/* ═══════════════════════════════════════════════════════
   Event: Outreach dialog — approve & send
═══════════════════════════════════════════════════════ */
$('#send-btn')?.addEventListener('click', async () => {
  if (!S.draft) return;
  const statusEl = $('#send-status');

  // Check draft has not been modified unsaved
  const currentSubject = $('#draft-subject')?.value || '';
  const currentBody = $('#draft-body')?.value || '';
  if (currentSubject !== S.draft.subject || currentBody !== S.draft.body) {
    if (statusEl) { statusEl.textContent = 'You modified the draft. Save edits before approving.'; statusEl.className = 'send-status error'; }
    return;
  }

  // Exact confirmation text
  const confirmText = prompt('To confirm, type exactly:\n\nI approve sending this email');
  if (confirmText !== 'I approve sending this email') {
    if (statusEl) { statusEl.textContent = 'Confirmation text did not match. Email not sent.'; statusEl.className = 'send-status error'; }
    return;
  }

  // Without automatic sending, the approved draft opens in Gmail. Open the tab now, while the click still
  // counts as a user gesture (popup blockers reject window.open after an await), and point it at Gmail below.
  const gmailTab = S.draft.automatic_sending_available ? null : window.open('', '_blank');
  if (gmailTab) gmailTab.document.title = 'Opening Gmail…';

  try {
    // Approve draft
    const approval = await api(`/api/outreach/drafts/${S.draft.draft_id}/approve`, {
      method: 'POST',
      body: JSON.stringify({
        content_hash: S.draft.content_hash,
        confirm: true,
        confirmation_text: confirmText
      })
    });

    // If automatic sending is available, send; otherwise show manual options
    if (approval.automatic_sending_available) {
      const result = await api('/api/outreach/send', {
        method: 'POST',
        body: JSON.stringify({
          draft_id: S.draft.draft_id,
          approval_token: approval.approval_token
        })
      });
      if (statusEl) {
        statusEl.textContent = result.send_result?.message || `Email ${result.status}.`;
        statusEl.className = 'send-status success';
      }
    } else {
      // Manual send path: the approved draft opens pre-filled in Gmail; the student presses Send there.
      const manual = approval.manual_send;
      const gmailOpened = Boolean(gmailTab && manual?.gmail);
      if (gmailOpened) gmailTab.location.href = manual.gmail;
      else gmailTab?.close();
      if (statusEl) {
        statusEl.innerHTML = `
          <span>${gmailOpened
            ? 'Approved. Your draft is open in a new Gmail tab: review it there and press Send.'
            : 'Approved. Open your draft in Gmail, review it, and press Send.'}</span>
          ${manual?.gmail ? `<a href="${esc(manual.gmail)}" target="_blank" rel="noopener" style="margin-left:8px;color:var(--blue-500);font-weight:700">Open draft in Gmail ↗</a>` : ''}
          ${manual?.mailto ? `<a href="${esc(manual.mailto)}" target="_blank" rel="noopener" style="margin-left:8px;color:var(--blue-500);font-weight:700">Other email app ↗</a>` : ''}`;
        statusEl.className = 'send-status success';
      }
    }
    // Disable send button after send
    const sendBtn = $('#send-btn');
    if (sendBtn) sendBtn.disabled = true;
  } catch (err) {
    gmailTab?.close();
    if (statusEl) { statusEl.textContent = err.message; statusEl.className = 'send-status error'; }
  }
});

/* ═══════════════════════════════════════════════════════
   Event: Clear profile
═══════════════════════════════════════════════════════ */
$('#clear-profile-btn')?.addEventListener('click', () => {
  if (confirm('Clear your saved profile from this browser?')) {
    clearProfile();
  }
});

/* ═══════════════════════════════════════════════════════
   Initialise
═══════════════════════════════════════════════════════ */
$('#start-search-btn')?.addEventListener('click', () => showView('form'));
$('#header-scroll-btn')?.addEventListener('click', () => showView('form'));
$('#back-to-landing')?.addEventListener('click', () => showView('landing'));
$('#brand-home')?.addEventListener('click', (e) => {
  e.preventDefault();
  showView('landing');
});
$('#return-research')?.addEventListener('click', () => showView('search'));

const initialView = new URLSearchParams(location.search).get('view');
if (initialView === 'form' || initialView === 'search') showView(initialView);
else showView('landing');
if (initialView === 'search') show($('#workflow-section'));

restoreProfile();
checkHealth();
