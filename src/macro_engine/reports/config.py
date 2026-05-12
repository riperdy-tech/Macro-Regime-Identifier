from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ReportConfig(BaseModel):
    output_dir: str = "outputs"
    include_feature_details: bool = True
    include_dimension_details: bool = True
    include_diagnostic_summary: bool = True
    max_contributors: int = Field(default=5, ge=1)


def load_report_config(path: str | Path) -> ReportConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return ReportConfig.model_validate(data.get("reports", {}))
