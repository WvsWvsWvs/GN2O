"""Small, explainable Bayesian mastery model for Anki concepts."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import math

@dataclass
class ConceptEstimate:
    concept: str
    reviews: int
    successes: int
    failures: int
    mastery_probability: float
    retention: float | None
    status: str

def estimate(concept: str, successes: int, failures: int, days_since_review: float | None = None, stability_days: float | None = None) -> ConceptEstimate:
    # Uniform Beta(1,1) prior; posterior mean with conservative data threshold.
    total = successes + failures
    probability = (1 + successes) / (2 + total)
    retention = None
    if days_since_review is not None and stability_days and stability_days > 0:
        retention = math.exp(-days_since_review / stability_days)
        probability *= retention
    status = "insufficient-data" if total < 5 else ("mastered" if probability >= 0.9 else "developing")
    return ConceptEstimate(concept, total, successes, failures, round(probability, 4), None if retention is None else round(retention, 4), status)

def analyze(tag_reviews: dict[str, tuple[int, int]]) -> list[dict]:
    return [asdict(estimate(tag, successes, failures)) for tag, (successes, failures) in sorted(tag_reviews.items())]

def forecast(estimates: list[dict], target: float = 0.90) -> dict:
    usable = [x for x in estimates if x["reviews"] >= 5]
    if len(usable) < 2:
        return {"status": "insufficient-data"}
    current = sum(x["mastery_probability"] for x in usable) / len(usable)
    gap = max(0.0, target - current)
    weeks = math.ceil(gap / 0.01) if gap else 0
    return {"status": "projected", "current": round(current, 4), "target": target, "weeks": weeks}
