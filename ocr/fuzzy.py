from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz


@dataclass(frozen=True)
class MatchResult:
    candidate: str
    score: float


def match(
    raw: str, candidates: list[str], threshold: float = 0.80
) -> MatchResult | None:
    best_candidate = ""
    best_score = 0.0
    for candidate in candidates:
        score = fuzz.ratio(raw.lower(), candidate.lower()) / 100.0
        if score > best_score:
            best_score = score
            best_candidate = candidate
    if best_score >= threshold:
        return MatchResult(candidate=best_candidate, score=best_score)
    return None
