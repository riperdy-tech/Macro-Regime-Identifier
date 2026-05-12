from __future__ import annotations

from pathlib import Path


def build_watchlist(dimension_scores: dict[str, float]) -> list[str]:
    watchlist: list[str] = []
    if (
        dimension_scores.get("inflation_pressure", 0) > 0.25
        and dimension_scores.get("growth_momentum", 0) < -0.20
    ):
        watchlist.append(
            "If inflation remains elevated while growth weakens, inflationary_slowdown risk may rise."
        )
    if (
        dimension_scores.get("financial_conditions", 0) < -0.35
        and dimension_scores.get("market_risk_appetite", 0) < -0.35
    ):
        watchlist.append(
            "If market stress and credit stress deepen together, crisis_risk_off probability may rise."
        )
    if (
        dimension_scores.get("growth_momentum", 0) < -0.25
        and dimension_scores.get("labor_tightness", 0) < -0.25
        and dimension_scores.get("financial_conditions", 0) < -0.25
    ):
        watchlist.append(
            "If labor conditions weaken while financial conditions stay tight, recessionary_deleveraging probability may rise."
        )
    if (
        dimension_scores.get("inflation_pressure", 0) < -0.20
        and dimension_scores.get("policy_stance", 0) > 0.20
        and dimension_scores.get("market_risk_appetite", 0) > 0.20
    ):
        watchlist.append(
            "If inflation keeps falling while policy and risk appetite improve, policy_easing_recovery probability may rise."
        )
    return watchlist[:4]


def build_markdown_report(payload: dict) -> str:
    probabilities = sorted(
        payload["regime_probabilities"].items(), key=lambda item: item[1], reverse=True
    )
    probability_lines = "\n".join(
        f"- {name}: {probability:.1%}" for name, probability in probabilities
    )
    dimension_lines = "\n".join(
        f"- {name}: {value['score']:.2f} confidence {value['confidence']:.2f}"
        for name, value in payload["dimension_scores"].items()
    )
    driver_lines = "\n".join(f"- {driver}" for driver in payload["top_drivers"])
    watchlist_lines = "\n".join(f"- {item}" for item in payload["watchlist"]) or "- None"
    health = payload["source_health"]
    return f"""# Macro Regime Report

As of: {payload["as_of"]}
Model version: {payload["model_version"]}
Historical mode: {payload["historical_mode"]}

Primary regime: {payload["primary_regime"]}
Secondary regime: {payload["secondary_regime"]}
Confidence: {payload["confidence"]:.2f}
Transition zone: {"yes" if payload["transition_zone"] else "no"}

## Why

{driver_lines}

## Regime Probabilities

{probability_lines}

## Dimension Scores

{dimension_lines}

## Watchlist

{watchlist_lines}

## Source Health

- Available series: {health["available_series"]}/{health["total_series"]}
- Stale series: {health["stale_series"]}
- Missing series: {health["missing_series"]}
- Disabled series: {health["disabled_series"]}

{payload["disclaimer"]}
"""


def write_markdown_report(markdown: str, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
