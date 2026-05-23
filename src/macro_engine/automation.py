"""Automation summary for GitHub Actions / scheduled runs.

Produces a small JSON + Markdown summary of the most recent run state
so scheduled workflows have a simple artifact to inspect.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import os
from typing import Any


def build_automation_summary(
    *,
    outputs_dir: str | Path = "outputs",
    dashboard_data_dir: str | Path = "dashboard/public/data",
) -> dict[str, Any]:
    """Build a JSON-serializable automation run summary."""
    outputs_dir = Path(outputs_dir)
    dashboard_data_dir = Path(dashboard_data_dir)

    now = datetime.now(UTC).isoformat()

    summary: dict[str, Any] = {
        "generated_at": now,
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_run_number": os.environ.get("GITHUB_RUN_NUMBER"),
        "github_sha": os.environ.get("GITHUB_SHA"),
    }

    # Macro regime
    regime_path = outputs_dir / "current_regime.json"
    if regime_path.exists():
        regime = json.loads(regime_path.read_text(encoding="utf-8"))
        summary["macro"] = {
            "date": regime.get("date"),
            "regime": regime.get("reported_regime"),
            "confidence": regime.get("reported_confidence"),
            "valid": regime.get("valid"),
        }
    else:
        summary["macro"] = {"status": "missing"}

    # Sector
    sector_path = outputs_dir / "current_sector_ranking.json"
    if sector_path.exists():
        sector = json.loads(sector_path.read_text(encoding="utf-8"))
        top3 = [
            {"rank": s["rank"], "sector_id": s["sector_id"]}
            for s in sector.get("ranking", [])[:3]
        ]
        summary["sector"] = {"top3": top3, "valid": sector.get("valid")}
    else:
        summary["sector"] = {"status": "missing"}

    # Dashboard manifest
    manifest_path = dashboard_data_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        summary["dashboard"] = {
            "data_status": manifest.get("data_status"),
            "missing_files": manifest.get("missing_files"),
            "latest_macro_date": manifest.get("latest_macro_date"),
            "latest_news_score_date": manifest.get("latest_news_score_date"),
        }
    else:
        summary["dashboard"] = {"status": "missing"}

    # Accumulation
    accum_path = outputs_dir / "news_accumulation_report.json"
    if accum_path.exists():
        accum = json.loads(accum_path.read_text(encoding="utf-8"))
        summary["accumulation"] = {
            "readiness_label": accum.get("readiness_label"),
            "classified_items": accum.get("total_classified_items"),
        }
    else:
        summary["accumulation"] = {"status": "missing"}

    return summary


def write_automation_summary(
    *,
    outputs_dir: str | Path = "outputs",
) -> tuple[Path, Path]:
    """Write automation run summary JSON and Markdown. Returns (json_path, md_path)."""
    output_dir = Path(outputs_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    data = build_automation_summary(outputs_dir=output_dir)

    json_path = output_dir / "automation_run_summary.json"
    json_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    md_lines = ["# Automation Run Summary", ""]
    md_lines.append(f"- **Generated**: {data.get('generated_at', 'N/A')}")
    if data.get("github_run_id"):
        md_lines.append(
            f"- **GitHub Run**: {data['github_run_id']} "
            f"(#{data.get('github_run_number', 'N/A')})"
        )
        md_lines.append(f"- **SHA**: {data.get('github_sha', 'N/A')}")
    md_lines.append("")

    macro = data.get("macro", {})
    if macro.get("regime"):
        md_lines.append(
            f"- **Macro Regime**: {macro['regime']} "
            f"(confidence: {macro.get('confidence', 0):.4f})"
        )
    else:
        md_lines.append("- **Macro**: not available")

    sector = data.get("sector", {})
    if sector.get("top3"):
        top = ", ".join(s["sector_id"] for s in sector["top3"])
        md_lines.append(f"- **Top Sectors**: {top}")
    else:
        md_lines.append("- **Sectors**: not available")

    dashboard = data.get("dashboard", {})
    if dashboard.get("data_status"):
        md_lines.append(f"- **Dashboard**: {dashboard['data_status']}")
    else:
        md_lines.append("- **Dashboard**: not available")

    accum = data.get("accumulation", {})
    if accum.get("readiness_label"):
        md_lines.append(
            f"- **Readiness**: {accum['readiness_label']} "
            f"({accum.get('classified_items', 'N/A')} classified)"
        )
    md_lines.append("")

    md_lines.append("This is an automated diagnostic summary.")
    md_lines.append(
        "It is not investment advice, market action guidance, "
        "execution guidance, or instructions for changing holdings."
    )

    md_path = output_dir / "automation_run_summary.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return json_path, md_path
