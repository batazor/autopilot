from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz


@dataclass(frozen=True)
class MatchResult:
    candidate: str
    score: float


def match(
    raw: str,
    candidates: list[str],
    threshold: float = 0.80,
    *,
    partial: bool = False,
) -> MatchResult | None:
    """Score ``raw`` against each candidate and return the best one above ``threshold``.

    ``partial=False`` (default) compares whole strings (``fuzz.ratio``) — the right
    choice when ``raw`` is the entire OCR'd content and we want it to equal the
    candidate end-to-end (screen detection, navigation hints, event aliases).

    ``partial=True`` matches the candidate as a substring (``fuzz.partial_ratio``)
    — the right choice for action=text overlay rules whose OCR region may pick
    up surrounding noise (multiple lines, sibling labels). Without this, OCR of
    a wide ``{region}_search`` ROI returns the target phrase glued to UI noise
    like "Patrick G C * Lv.5 Lv.5 Lv.5 Lv. 5 Tap anywhere to continue" which
    drags the whole-string ratio below any reasonable threshold.
    """
    scorer = fuzz.partial_ratio if partial else fuzz.ratio
    best_candidate = ""
    best_score = 0.0
    for candidate in candidates:
        score = scorer(raw.lower(), candidate.lower()) / 100.0
        if score > best_score:
            best_score = score
            best_candidate = candidate
    if best_score >= threshold:
        return MatchResult(candidate=best_candidate, score=best_score)
    return None
