# GradBeacon frontend (next phase)

Contract: the backend API in `../README.md` §5 and the live OpenAPI at `/openapi.json`.

- Progressive results: SSE `GET /api/research/{id}/events` (`program`, `program_updated`, trace events, `done`);
  fall back to polling `GET /api/research/{id}?since_seq=N` if SSE is buffered.
- Must implement: landing page + profile form (required vs optional, FR-01–08), evidence panel shown prominently,
  local filters/sorting without API calls (unknown amounts sort last, never as 0 — use `match.sort_*` fields),
  favorites + "remember my profile" in localStorage with session/in-memory fallback and a clear-profile button,
  "Needs your decision / Verify this" panel, shortlist approval, professor list, draft editor with an
  approve-and-send confirmation dialog collecting the exact text "I approve sending this email", sent/failed status,
  and the mailto/copy handoff while automatic sending is deferred.
- Hosting: build to `frontend/dist`; FastAPI serves it automatically at `/` when `frontend/dist/index.html` exists.
