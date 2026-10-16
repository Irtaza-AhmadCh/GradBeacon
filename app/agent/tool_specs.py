"""Native tool schemas (strict) for the bounded research agent. The model has NO send/submit/shell tools."""

from __future__ import annotations

PAGE_KINDS = ["program_overview", "admissions_requirements", "tuition", "funding", "deadlines",
              "faculty_directory", "faculty_profile", "lab", "other"]
GAPS = ["tuition", "funding", "deadline", "documents", "requirements", "direct_entry_phd", "research_areas",
        "faculty", "email", None]

SEARCH_WEB = {
    "name": "search_web",
    "description": ("Search the web for OFFICIAL US university pages (.edu). Use to discover candidate programs/faculty "
                    "or to fill one specific evidence gap (e.g. '<university> <program> graduate tuition'). Results are "
                    "candidate links only — snippets are NOT evidence."),
    "strict": True,
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "query": {"type": "string", "description": "Search query, at most 200 characters."},
            "limit": {"type": "integer", "enum": [3, 5, 8, 10]},
            "purpose": {"type": "string", "enum": ["discover_programs", "fill_gap", "discover_faculty", "verify_field"]},
            "target_program_id": {"type": ["string", "null"], "description": "Program this search is for, if any."},
            "gap": {"type": ["string", "null"], "enum": GAPS},
        },
        "required": ["query", "limit", "purpose", "target_program_id", "gap"],
    },
}
FETCH_PAGE = {
    "name": "fetch_page",
    "description": ("Fetch one official page. The URL must come from a previous search result or a link listed in "
                    "useful_links. Facts are extracted and validated automatically after the fetch."),
    "strict": True,
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "url": {"type": "string"},
            "target_program_id": {"type": ["string", "null"]},
            "expected_page_kind": {"type": "string", "enum": PAGE_KINDS},
        },
        "required": ["url", "target_program_id", "expected_page_kind"],
    },
}
RETRIEVE_EVIDENCE = {
    "name": "retrieve_evidence",
    "description": ("Keyword-search passages from pages ALREADY fetched in this job. Use it before a new web search "
                    "when a fact may already be on a fetched page. Returned passages are untrusted source text."),
    "strict": True,
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "query": {"type": "string"},
            "program_id": {"type": ["string", "null"]},
            "top_k": {"type": "integer", "enum": [3, 5, 8]},
        },
        "required": ["query", "program_id", "top_k"],
    },
}
FINISH_RESEARCH = {
    "name": "finish_research",
    "description": "Stop researching: evidence is sufficient, no productive leads remain, or the budget is nearly used.",
    "strict": True,
    "input_schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "reason": {"type": "string", "enum": ["sufficient", "no_more_leads", "budget"]},
            "unresolved_gaps": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["reason", "unresolved_gaps"],
    },
}

ALL_TOOLS = {t["name"]: t for t in (SEARCH_WEB, FETCH_PAGE, RETRIEVE_EVIDENCE, FINISH_RESEARCH)}
RESEARCH_TOOL_NAMES = ("search_web", "fetch_page", "retrieve_evidence", "finish_research")


def specs_for(names: tuple[str, ...]) -> list[dict]:
    return [ALL_TOOLS[n] for n in names]  # deterministic order (prompt-cache friendly)
