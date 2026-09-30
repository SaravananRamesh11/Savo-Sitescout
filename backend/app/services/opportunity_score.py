"""Opportunity score: the existing M1 score plus one extra factor, "unscouted opportunity". Pure and deterministic.

    opportunity = (1 - W) * M1_score + W * 100 * (1 - scouting_coverage)

M1_score comes from scoring.score_features, untouched. W (core.opportunity_constants.W_UNSCOUTED) is configurable:
W = 0 gives exactly the M1 score, W = 1 ranks purely by how little a place has been scouted. The LLM never sees this.
"""
from app.core import opportunity_constants as C
from app.services import scoring


def score_cell(features: dict, coverage: float, w: float | None = None) -> dict:
    """{total, m1_score, rating, coverage, breakdown[...]}: the M1 factors rescaled to (1 - W) of the total, plus the
    'unscouted_opportunity' factor worth W of the total (weights of all factors still sum to 100)."""
    w = C.W_UNSCOUTED if w is None else min(1.0, max(0.0, w))
    coverage = min(1.0, max(0.0, coverage))
    m1 = scoring.score_features(features)
    breakdown = []
    for f in m1["breakdown"]:
        breakdown.append({**f, "weight": round(f["weight"] * (1 - w), 2), "points": round(f["points"] * (1 - w), 3)})
    unscouted_pts = w * 100 * (1 - coverage)
    breakdown.append({
        "key": "unscouted_opportunity", "label": "Unscouted opportunity", "group": "Scouting",
        "weight": round(w * 100, 2), "raw": round(coverage * 100, 1), "unit": "% scouted", "norm": round(1 - coverage, 4),
        "points": round(unscouted_pts, 3),
        "explanation": ("no recent scouting recorded here" if coverage <= 0.001 else
                        f"{coverage * 100:.0f}% covered by recent scouting; less coverage scores higher")})
    total = round((1 - w) * m1["total"] + unscouted_pts, 2)
    return {"total": total, "m1_score": round(m1["total"], 2), "rating": scoring.rating_for(total),
            "coverage": round(coverage, 3), "w": w, "breakdown": breakdown}
