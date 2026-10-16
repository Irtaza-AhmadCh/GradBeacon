"""Deterministic normalization of published strings. Never returns 0 for missing amounts."""

from __future__ import annotations

import re
from datetime import date

from app.models.common import Residency, TuitionPeriod

_MONEY = re.compile(r"(?:(USD|US\$|\$|EUR|€|GBP|£)\s?)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
_MON_ABBR = {k[:3]: v for k, v in _MONTHS.items()}


def parse_money(s: str) -> tuple[float, str] | None:
    if not s:
        return None
    m = _MONEY.search(s)
    if not m:
        return None
    sym = m.group(1).upper()
    cur = {"$": "USD", "US$": "USD", "USD": "USD", "€": "EUR", "EUR": "EUR", "£": "GBP", "GBP": "GBP"}.get(sym, "USD")
    amount = float(m.group(2).replace(",", "") + ("." + m.group(3) if m.group(3) else ""))
    if amount <= 0:
        return None
    return amount, cur


def digit_groups(amount_text: str) -> list[str]:
    m = _MONEY.search(amount_text or "")
    if not m:
        return re.findall(r"\d[\d,]*", amount_text or "")
    return [m.group(2)]


def parse_period(s: str) -> TuitionPeriod:
    s = (s or "").lower()
    if re.search(r"per\s+(credit|unit|hour|credit hour)|/\s*credit|each credit", s):
        return TuitionPeriod.per_credit
    if re.search(r"per\s+semester|/\s*semester|each semester|semester", s):
        return TuitionPeriod.per_semester
    if re.search(r"per\s+(quarter|term)|/\s*(quarter|term)|quarter", s):
        return TuitionPeriod.per_term
    if re.search(r"per\s+(year|annum)|annual|academic year|/\s*year|yearly|per academic year", s):
        return TuitionPeriod.per_year
    if re.search(r"total|entire program|whole program|program cost", s):
        return TuitionPeriod.total_program
    return TuitionPeriod.other


def parse_residency(s: str) -> Residency:
    s = (s or "").lower()
    if "international" in s:
        return Residency.international
    if re.search(r"non-?resident|out[- ]of[- ]state", s):
        return Residency.out_of_state
    if re.search(r"\bresident\b|in[- ]state", s):
        return Residency.in_state
    if re.search(r"\ball\b", s):
        return Residency.all
    return Residency.unspecified


def annualize(amount: float | None, period: TuitionPeriod) -> float | None:
    """Only for sorting/scoring. Never invents credit counts or program length."""
    if amount is None:
        return None
    if period == TuitionPeriod.per_year:
        return amount
    if period == TuitionPeriod.per_semester:
        return amount * 2
    return None


def _year_from(text: str) -> int | None:
    m = re.search(r"\b(20\d{2})\b", text or "")
    return int(m.group(1)) if m else None


def parse_date(s: str, qualifiers: str | None = None) -> date | None:
    """'December 1, 2026' -> date. 'December 1' + 'Fall 2027' -> Dec 1 2026 (deadline precedes term)."""
    text = (s or "").lower()
    m = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
                  r"(?:,?\s*(20\d{2}))?", text)
    if not m:
        return None
    month = _MON_ABBR[m.group(1)[:3]]
    day = int(m.group(2))
    year = int(m.group(3)) if m.group(3) else None
    if year is None:
        term_year = _year_from(qualifiers or "")
        term = (qualifiers or "").lower()
        if term_year is None:
            return None
        year = term_year - 1 if ("fall" in term and month >= 9) else term_year
        if "spring" in term and month >= 8:
            year = term_year - 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


def date_tokens_present(value: str, quote: str) -> bool:
    d = parse_date(value)
    q = quote.lower()
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})", value.lower())
    if m:
        return m.group(1) in q and re.search(rf"\b0?{int(m.group(2))}(?:st|nd|rd|th)?\b", q) is not None
    if d:
        return str(d.day) in q
    nums = re.findall(r"\d+", value)
    return all(n in q for n in nums)


def parse_gpa_min(s: str) -> tuple[float, str] | None:
    s = s or ""
    m = re.search(r"(\d(?:\.\d{1,2})?)\s*(?:/|out of|on a|on the)?\s*(4\.0|4|5\.0|10\.0|10|100)?(?:\s*(?:-point)?\s*scale)?",
                  s)
    if not m:
        return None
    val = float(m.group(1))
    scale = m.group(2)
    if scale is None:
        scale = "4.0" if val <= 4.0 else None
    if scale is None:
        return None
    scale = {"4": "4.0", "10": "10.0"}.get(scale, scale)
    return val, scale


_TESTS = [("TOEFL_iBT", r"toefl"), ("IELTS", r"ielts"), ("Duolingo", r"duolingo|\bdet\b"), ("PTE", r"\bpte\b")]


def parse_test_mins(s: str) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    low = (s or "").lower()
    for name, pat in _TESTS:
        m = re.search(rf"(?:{pat})[^0-9]{{0,40}}(\d{{1,3}}(?:\.\d)?)", low)
        if m:
            out.append((name, float(m.group(1))))
    return out
