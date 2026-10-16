"""Scoring configuration v1. Initial, documented, NOT empirically validated (agent spec §8.1)."""

SCORE_VERSION = "v1"
WEIGHTS = {"academic": 0.35, "research": 0.30, "funding": 0.20, "preference": 0.15}

GPA_BELOW_MARGIN = 0.2          # within this below the published minimum -> partial credit
GPA_SCORES = {"meets": 100, "slightly_below": 60, "below": 25}
ENGLISH_SCORES = {"meets": 100, "below": 30}
FUNDING_SCORES = {
    "need_full_funding": {"guaranteed": 100, "available": 50, "not_offered": 5},
    "prefer_funding": {"guaranteed": 100, "available": 75, "not_offered": 30},
}
BUDGET_SCORES = {"within": 100, "within_20pct": 50, "over": 10}
STATE_SCORES = {"preferred": 100, "not_preferred": 0}
MIN_KNOWN_DIMENSIONS = 2
