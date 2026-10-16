"""Deterministic, explainable profile-fit scoring. NOT an admission probability (FR-21/23/24)."""

from __future__ import annotations

from app.models.common import DegreeLevel, FieldStatus, FundingAvailability, FundingPreference, Residency
from app.models.profile import StudentProfile
from app.models.program import ProgramOpportunity
from app.models.results import DimensionScore, MatchResult
from app.scoring import weights as W
from app.scoring.terms import extract_terms
from app.validation.status import verified_field_count

_OK = (FieldStatus.verified, FieldStatus.curated_fallback)


def _dim(scores: list[tuple[int, str]], reasons_unknown: list[str], weight: float, uncertain=False) -> DimensionScore:
    if not scores:
        return DimensionScore(score=None, status="unknown", reasons=reasons_unknown, weight=weight)
    avg = round(sum(s for s, _ in scores) / len(scores))
    return DimensionScore(score=avg, status="uncertain" if uncertain else "known",
                          reasons=[r for _, r in scores] + reasons_unknown, weight=weight)


def funding_class(p: ProgramOpportunity) -> str:
    ok = [f for f in p.funding if f.claim.status in _OK]
    if any(f.availability == FundingAvailability.guaranteed_for_admitted for f in ok):
        return "guaranteed"
    if any(f.availability in (FundingAvailability.appears_available, FundingAvailability.mentioned) for f in ok):
        return "available"
    if ok:
        return "not_offered"
    if p.funding_status == FieldStatus.not_found:
        return "none_found"
    return "unknown"


def best_tuition(p: ProgramOpportunity):
    order = [Residency.international, Residency.out_of_state, Residency.all, Residency.unspecified]
    cands = [t for t in p.tuition if t.claim.status in _OK and t.annualized_usd is not None]
    cands.sort(key=lambda t: order.index(t.residency) if t.residency in order else 99)
    return cands[0] if cands else None


class Scorer:
    version = W.SCORE_VERSION

    def score(self, profile: StudentProfile, p: ProgramOpportunity) -> MatchResult:
        matches, gaps, unknowns = [], [], []

        # ---- academic ----
        acad: list[tuple[int, str]] = []
        acad_unknown: list[str] = []
        gpa_req = next((r for r in p.requirements if r.key == "min_gpa" and r.claim.status in _OK
                        and r.numeric_min is not None), None)
        if profile.gpa is None:
            acad_unknown.append("GPA not provided — not compared")
        elif gpa_req is None:
            acad_unknown.append("Published minimum GPA not found")
        elif profile.gpa.scale is None or profile.gpa.scale != gpa_req.numeric_scale:
            acad_unknown.append("Grading scale differs or is unknown; GPA comparison not exact")
        else:
            g, m = profile.gpa.value, gpa_req.numeric_min
            if g >= m:
                acad.append((W.GPA_SCORES["meets"], f"GPA {g} meets the published minimum {m}"))
                matches.append(f"GPA meets published minimum ({m})")
            elif g >= m - W.GPA_BELOW_MARGIN:
                acad.append((W.GPA_SCORES["slightly_below"], f"GPA {g} is slightly below the published minimum {m}"))
                gaps.append("GPA slightly below published minimum")
            else:
                acad.append((W.GPA_SCORES["below"], f"GPA {g} is below the published minimum {m}"))
                gaps.append("GPA below published minimum")
        eng_reqs = [r for r in p.requirements if r.key == "english_test" and r.claim.status in _OK
                    and r.numeric_min is not None and r.test]
        if profile.language_test_scores and eng_reqs:
            compared = False
            for s in profile.language_test_scores:
                req = next((r for r in eng_reqs if r.test == s.test), None)
                if req:
                    compared = True
                    if s.overall >= req.numeric_min:
                        acad.append((W.ENGLISH_SCORES["meets"], f"{s.test} {s.overall} meets minimum {req.numeric_min}"))
                        matches.append(f"{s.test} score meets published minimum")
                    else:
                        acad.append((W.ENGLISH_SCORES["below"], f"{s.test} {s.overall} below minimum {req.numeric_min}"))
                        gaps.append(f"{s.test} score below published minimum")
            if not compared:
                acad_unknown.append("Your English test differs from the published test minimums")
        elif not profile.language_test_scores:
            acad_unknown.append("English test score not provided")
        else:
            acad_unknown.append("Published English test minimum not found")
        de = next((r for r in p.requirements if r.key == "direct_entry_phd" and r.claim.status in _OK), None)
        if p.degree_level == DegreeLevel.phd:
            if de and de.test == "bachelor_ok":
                acad.append((100, "Official source indicates admission directly after a bachelor's degree"))
                matches.append("Direct PhD entry after a bachelor's supported by official source")
            elif de is None:
                unknowns.append("Direct PhD entry after a bachelor's degree not confirmed")
        academic = _dim(acad, acad_unknown, W.WEIGHTS["academic"])

        # ---- research ----
        interests = profile.research_interests or ([profile.specialization] if profile.specialization else [])
        program_text = " ".join([p.program_name, p.field] + [c.value or "" for c in p.research_areas if c.status in _OK])
        prog_terms = extract_terms(program_text)
        if interests:
            hit = [i for i in interests[:5] if extract_terms(i) & prog_terms]
            if not p.research_areas and not hit:
                research = DimensionScore(score=None, status="unknown", weight=W.WEIGHTS["research"],
                                          reasons=["No verified research-area text found for this program"])
                unknowns.append("Research areas not verified")
            else:
                sc = min(100, round(100 * len(hit) / max(1, min(len(interests), 5))))
                reasons = [f"{len(hit)} of {min(len(interests), 5)} of your interests appear in verified program text"]
                if hit:
                    reasons.append("Matched: " + ", ".join(hit))
                    matches.append("Research interests overlap: " + ", ".join(hit))
                research = DimensionScore(score=sc, status="known" if p.research_areas else "uncertain",
                                          reasons=reasons, weight=W.WEIGHTS["research"])
        else:
            ov = extract_terms(profile.field) & prog_terms
            sc = 100 if ov else 30
            research = DimensionScore(score=sc, status="uncertain", weight=W.WEIGHTS["research"],
                                      reasons=["No research interests given; compared your field with the program name only"])

        # ---- funding ----
        fc = funding_class(p)
        pref = profile.funding_preference
        if pref in (FundingPreference.not_required, FundingPreference.unspecified):
            funding = DimensionScore(score=None, status="unknown", weight=W.WEIGHTS["funding"],
                                     reasons=["No funding preference given"])
        elif fc in ("guaranteed", "available"):
            sc = W.FUNDING_SCORES[pref.value][fc]
            label = ("Official source says funding is provided to admitted students" if fc == "guaranteed"
                     else "Funding is mentioned by an official source (your eligibility is not confirmed)")
            funding = DimensionScore(score=sc, status="known", reasons=[label], weight=W.WEIGHTS["funding"])
            matches.append(label)
        elif fc == "not_offered":
            sc = W.FUNDING_SCORES[pref.value]["not_offered"]
            label = "Official source states this program does not offer funding"
            funding = DimensionScore(score=sc, status="known", reasons=[label], weight=W.WEIGHTS["funding"])
            gaps.append(label)
        else:
            reason = ("No funding information found on official pages checked" if fc == "none_found"
                      else "Funding information not yet verified")
            funding = DimensionScore(score=None, status="unknown", reasons=[reason], weight=W.WEIGHTS["funding"])
            unknowns.append(reason)

        # ---- preferences ----
        prefs: list[tuple[int, str]] = []
        pref_unknown: list[str] = []
        budget = profile.budget.max_tuition_per_year_usd if profile.budget else None
        bt = best_tuition(p)
        if budget is not None:
            if bt is None:
                pref_unknown.append("No comparable annual tuition amount verified")
                unknowns.append("Tuition not comparable to your budget")
            else:
                a = bt.annualized_usd
                if a <= budget:
                    prefs.append((W.BUDGET_SCORES["within"], f"Tuition ~${a:,.0f}/yr is within your budget"))
                    matches.append("Tuition within budget")
                elif a <= budget * 1.2:
                    prefs.append((W.BUDGET_SCORES["within_20pct"], f"Tuition ~${a:,.0f}/yr is up to 20% over budget"))
                    gaps.append("Tuition slightly over budget")
                else:
                    prefs.append((W.BUDGET_SCORES["over"], f"Tuition ~${a:,.0f}/yr exceeds your budget"))
                    gaps.append("Tuition over budget")
        if profile.preferred_states:
            if p.state:
                if p.state in profile.preferred_states:
                    prefs.append((W.STATE_SCORES["preferred"], f"Located in preferred state {p.state}"))
                    matches.append(f"In preferred state {p.state}")
                else:
                    prefs.append((W.STATE_SCORES["not_preferred"], f"Located in {p.state}, not a preferred state"))
                    gaps.append("Not in a preferred state")
            else:
                pref_unknown.append("Program state not verified")
        preference = _dim(prefs, pref_unknown or (["No location or budget preference given"] if not prefs else []),
                          W.WEIGHTS["preference"])

        dims = {"academic": academic, "research": research, "funding": funding, "preference": preference}
        known = {k: d for k, d in dims.items() if d.score is not None}
        if len(known) < W.MIN_KNOWN_DIMENSIONS:
            overall = None
            unknowns.append("Insufficient verified data to compute an overall score")
        else:
            overall = round(sum(d.weight * d.score for d in known.values()) / sum(d.weight for d in known.values()))
        for d in (academic, preference):
            for r in d.reasons:
                if r not in unknowns and d.score is None:
                    unknowns.append(r)
        tuition_sort = bt.annualized_usd if bt else None
        fund_amounts = [f.amount for f in p.funding if f.amount and f.claim.status in _OK]
        return MatchResult(
            opportunity_id=p.program_id, overall_fit_score=overall, coverage=f"{len(known)}/4 dimensions",
            academic_fit=academic, research_fit=research, funding_fit=funding, preference_fit=preference,
            known_matches=_uniq(matches), known_gaps=_uniq(gaps), unknowns=_uniq(unknowns),
            score_version=self.version, sort_tuition_annual_usd=tuition_sort,
            sort_funding_amount_usd=max(fund_amounts) if fund_amounts else None, funding_class=fc,
            verified_field_count=verified_field_count(p),
        )


def _uniq(xs: list[str]) -> list[str]:
    out: list[str] = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out


def rank(pairs: list[tuple[ProgramOpportunity, MatchResult]]) -> list[tuple[ProgramOpportunity, MatchResult]]:
    return sorted(pairs, key=lambda pm: (pm[1].overall_fit_score is None, -(pm[1].overall_fit_score or 0),
                                         -pm[1].verified_field_count, pm[0].institution_name))
