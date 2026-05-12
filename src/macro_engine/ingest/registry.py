from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from macro_engine.ingest.schemas import IngestionSource


def load_ingestion_sources(path: str | Path) -> list[IngestionSource]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data: dict[str, Any] = yaml.safe_load(handle)
    sources = [IngestionSource.model_validate(item) for item in data.get("sources", [])]
    series_ids = [source.series_id for source in sources]
    duplicates = {series_id for series_id in series_ids if series_ids.count(series_id) > 1}
    if duplicates:
        raise ValueError(f"duplicate ingestion source IDs: {sorted(duplicates)}")
    return sources


def select_sources(
    sources: list[IngestionSource],
    requested_series: list[str] | None = None,
) -> list[IngestionSource]:
    if not requested_series:
        return [source for source in sources if source.enabled]
    source_map = {source.series_id: source for source in sources}
    selected: list[IngestionSource] = []
    for series_id in requested_series:
        if series_id in source_map:
            selected.append(source_map[series_id])
        else:
            selected.append(
                IngestionSource(
                    series_id=series_id,
                    name=series_id,
                    provider="FRED",
                    dimension="ad_hoc",
                    frequency="daily",
                    required=False,
                    enabled=True,
                    stale_after_days=5,
                    unusable_after_days=15,
                )
            )
    return selected
