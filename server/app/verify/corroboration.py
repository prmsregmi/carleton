"""Deterministic corroboration backstop.

The brain (LLM) computes the primary profile_match_score; this provides a
cheap token-overlap fallback when the model omits it, so a Verdict always
carries a defensible number.
"""

import re


def _tokens(s: str | None) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def token_overlap(a: str | None, b: str | None) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def fallback_match_score(anchors: dict, profile_text: str) -> float:
    """Best agreement of the name/company anchors against found profile text."""
    score = 0.0
    for key in ("name", "company"):
        value = anchors.get(key)
        if value:
            score = max(score, token_overlap(value, profile_text))
    return round(score, 3)
