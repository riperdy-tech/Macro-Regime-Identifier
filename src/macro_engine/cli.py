from __future__ import annotations

from pathlib import Path
import json
from typing import Annotated

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from macro_engine.config.loader import load_all_configs
from macro_engine.diagnostics.service import run_stored_historical_diagnostic
from macro_engine.dimensions.service import build_stored_dimensions
from macro_engine.evaluation.service import (
    build_stored_asof_features,
    build_stored_evaluation_calendar,
)
from macro_engine.experiments.runner import run_calibration_experiments
from macro_engine.features.service import build_stored_features
from macro_engine.ingest.fred import FredError
from macro_engine.ingest.service import run_fred_ingestion
from macro_engine.outputs.json_writer import write_json
from macro_engine.outputs.report import build_markdown_report, write_markdown_report
from macro_engine.pipeline import classify_observations
from macro_engine.pipeline_runner import run_pipeline as run_full_pipeline
from macro_engine.regimes.service import build_stored_regimes
from macro_engine.reports.service import (
    write_current_regime_report as write_current_regime_report_service,
    write_historical_diagnostic_report as write_historical_diagnostic_report_service,
)
from macro_engine.sectors.report import write_current_sector_report
from macro_engine.sectors.service import build_stored_sector_scores
from macro_engine.sectors.validation import (
    ingest_sector_proxy_prices,
    run_stored_sector_validation,
)
from macro_engine.sectors.validation_report import write_sector_validation_report
from macro_engine.storage.duckdb_store import DuckDBStore
from macro_engine.toy_data import build_toy_observations

app = typer.Typer(help="Macro Regime Intelligence Engine")
console = Console()


@app.command()
def validate_config(config_dir: str = "config") -> None:
    """Validate the Phase A config contract."""
    config = load_all_configs(config_dir)
    console.print(
        f"[green]Config valid[/green]: {len(config.sources)} sources, "
        f"{len(config.dimensions)} dimensions, {len(config.regimes)} regimes"
    )


@app.command()
def run_toy(
    as_of: str = "2026-05-08",
    config_dir: str = "config",
    output_dir: str = "data/outputs",
) -> None:
    """Run the tiny offline Phase A classification path."""
    config = load_all_configs(config_dir)
    result = classify_observations(build_toy_observations(), config, as_of)
    output_path = Path(output_dir)
    json_path = output_path / f"toy_regime_{as_of}.json"
    markdown_path = output_path / f"toy_regime_{as_of}.md"
    write_json(result["payload"], json_path)
    report = build_markdown_report(result["payload"])
    write_markdown_report(report, markdown_path)
    console.print(report)
    console.print(f"[green]Wrote[/green] {json_path}")
    console.print(f"[green]Wrote[/green] {markdown_path}")


@app.command()
def ingest(
    config: Annotated[str, typer.Option("--config")] = "config/phase_b_sources.yaml",
    series: Annotated[list[str] | None, typer.Option("--series")] = None,
    start: Annotated[str | None, typer.Option("--start")] = None,
    end: Annotated[str | None, typer.Option("--end")] = None,
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
    parquet_dir: Annotated[str, typer.Option("--parquet-dir")] = "data/raw/fred",
) -> None:
    """Phase B: ingest selected FRED series into DuckDB and Parquet."""
    try:
        summary = run_fred_ingestion(
            config_path=config,
            requested_series=series,
            start=start,
            end=end,
            db_path=db_path,
            parquet_dir=parquet_dir,
        )
    except FredError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print_json(data=summary.model_dump())


@app.command()
def health(db_path: str = "data/macro_engine.duckdb") -> None:
    """Show latest source health from local storage."""
    store = DuckDBStore(db_path)
    table = store.read_table("source_health")
    rich_table = Table(title="Source Health")
    columns = [
        "series_id",
        "last_observation_date",
        "days_since_last_observation",
        "expected_frequency",
        "stale_flag",
        "usable",
        "reason",
    ]
    for column in columns:
        rich_table.add_column(column)
    for row in table.sort_values("series_id").to_dict(orient="records"):
        rich_table.add_row(*(str(row.get(column)) for column in columns))
    console.print(rich_table)


@app.command()
def inspect_series(
    series_id: str,
    db_path: str = "data/macro_engine.duckdb",
    limit: int = 5,
) -> None:
    """Inspect stored metadata and latest raw observations for one series."""
    store = DuckDBStore(db_path)
    metadata = store.read_table("series_metadata")
    metadata = metadata[metadata["series_id"] == series_id]
    observations = store.read_raw_observations(series_id).tail(limit)
    console.print(f"[bold]{series_id} metadata[/bold]")
    if metadata.empty:
        console.print("[yellow]No metadata found[/yellow]")
    else:
        console.print_json(json.dumps(metadata.iloc[0].to_dict(), default=str))
    console.print(f"[bold]{series_id} latest observations[/bold]")
    if observations.empty:
        console.print("[yellow]No observations found[/yellow]")
    else:
        console.print(observations.to_string(index=False))


@app.command()
def build_features(
    config: Annotated[str, typer.Option("--config")] = "config/phase_b_sources.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
    parquet_dir: Annotated[str, typer.Option("--parquet-dir")] = "data/raw/fred",
) -> None:
    """Phase C: build transformed and normalized feature rows from stored raw data."""
    result = build_stored_features(config_path=config, db_path=db_path, parquet_dir=parquet_dir)
    console.print_json(
        data={
            "features_total": int(len(result.features)),
            "features_valid": int(result.features["valid"].fillna(False).sum()),
            "feature_definitions": int(len(result.feature_health)),
            "usable_features": int(result.feature_health["usable"].fillna(False).sum()),
        }
    )


@app.command()
def feature_health(db_path: str = "data/macro_engine.duckdb") -> None:
    """Show latest feature health from local storage."""
    store = DuckDBStore(db_path)
    table = store.read_table("feature_health")
    rich_table = Table(title="Feature Health")
    columns = [
        "feature_id",
        "series_id",
        "enabled",
        "valid_count",
        "invalid_count",
        "latest_valid_date",
        "usable",
        "reason",
    ]
    for column in columns:
        rich_table.add_column(column)
    for row in table.sort_values("feature_id").to_dict(orient="records"):
        rich_table.add_row(*(str(row.get(column)) for column in columns))
    console.print(rich_table)


@app.command()
def inspect_feature(
    feature_id: str,
    db_path: str = "data/macro_engine.duckdb",
    limit: int = 10,
) -> None:
    """Inspect latest stored feature rows for one feature."""
    store = DuckDBStore(db_path)
    features = store.read_features(feature_id).tail(limit)
    if features.empty:
        console.print("[yellow]No feature rows found[/yellow]")
        raise typer.Exit(code=1)
    console.print(features.to_string(index=False))


@app.command()
def build_evaluation_calendar(
    config: Annotated[str, typer.Option("--config")] = "config/phase_b_sources.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
    parquet_dir: Annotated[str, typer.Option("--parquet-dir")] = "data/raw/fred",
) -> None:
    """Phase J: build configured macro evaluation dates and as-of feature values."""
    result = build_stored_evaluation_calendar(
        config_path=config,
        db_path=db_path,
        parquet_dir=parquet_dir,
    )
    console.print_json(
        data={
            "evaluation_dates": int(len(result.evaluation_calendar)),
            "asof_feature_rows": int(len(result.asof_feature_values)),
            "valid_asof_feature_rows": int(
                result.asof_feature_values["valid"].fillna(False).sum()
            )
            if not result.asof_feature_values.empty
            else 0,
        }
    )


@app.command()
def build_asof_features(
    config: Annotated[str, typer.Option("--config")] = "config/phase_b_sources.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
    parquet_dir: Annotated[str, typer.Option("--parquet-dir")] = "data/raw/fred",
) -> None:
    """Phase J: align stored features onto the configured evaluation calendar."""
    result = build_stored_asof_features(
        config_path=config,
        db_path=db_path,
        parquet_dir=parquet_dir,
    )
    console.print_json(
        data={
            "evaluation_dates": int(len(result.evaluation_calendar)),
            "asof_feature_rows": int(len(result.asof_feature_values)),
            "valid_asof_feature_rows": int(
                result.asof_feature_values["valid"].fillna(False).sum()
            )
            if not result.asof_feature_values.empty
            else 0,
        }
    )


@app.command()
def inspect_asof_feature(
    feature_id: str,
    db_path: str = "data/macro_engine.duckdb",
    limit: int = 10,
) -> None:
    """Inspect latest calendar-aligned as-of rows for one feature."""
    store = DuckDBStore(db_path)
    features = store.read_asof_feature_values(feature_id).tail(limit)
    if features.empty:
        console.print("[yellow]No as-of feature rows found[/yellow]")
        raise typer.Exit(code=1)
    console.print(features.to_string(index=False))


@app.command()
def build_dimensions(
    config: Annotated[str, typer.Option("--config")] = "config/phase_b_sources.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
    parquet_dir: Annotated[str, typer.Option("--parquet-dir")] = "data/raw/fred",
) -> None:
    """Phase D: build dimension contributions, scores, and health from stored features."""
    result = build_stored_dimensions(config_path=config, db_path=db_path, parquet_dir=parquet_dir)
    console.print_json(
        data={
            "contribution_rows": int(len(result.contributions)),
            "dimension_score_rows": int(len(result.dimension_scores)),
            "valid_dimension_rows": int(result.dimension_scores["valid"].fillna(False).sum()),
            "dimension_health_rows": int(len(result.dimension_health)),
        }
    )


@app.command()
def inspect_dimension(
    dimension_id: str,
    db_path: str = "data/macro_engine.duckdb",
    limit: int = 10,
) -> None:
    """Inspect latest stored dimension scores for one dimension."""
    store = DuckDBStore(db_path)
    scores = store.read_dimension_scores(dimension_id).tail(limit)
    if scores.empty:
        console.print("[yellow]No dimension score rows found[/yellow]")
        raise typer.Exit(code=1)
    console.print(scores.to_string(index=False))


@app.command()
def dimension_health(db_path: str = "data/macro_engine.duckdb") -> None:
    """Show latest dimension health rows from local storage."""
    store = DuckDBStore(db_path)
    table = store.read_table("dimension_health")
    rich_table = Table(title="Dimension Health")
    columns = [
        "dimension_id",
        "date",
        "valid",
        "valid_feature_count",
        "required_feature_count",
        "reason",
    ]
    for column in columns:
        rich_table.add_column(column)
    for row in table.sort_values(["dimension_id", "date"]).tail(30).to_dict(orient="records"):
        rich_table.add_row(*(str(row.get(column)) for column in columns))
    console.print(rich_table)


@app.command()
def build_regimes(
    config: Annotated[str, typer.Option("--config")] = "config/phase_b_sources.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
    parquet_dir: Annotated[str, typer.Option("--parquet-dir")] = "data/raw/fred",
) -> None:
    """Phase E: build regime contributions, scores, probabilities, and health."""
    result = build_stored_regimes(config_path=config, db_path=db_path, parquet_dir=parquet_dir)
    console.print_json(
        data={
            "contribution_rows": int(len(result.contributions)),
            "regime_score_rows": int(len(result.regime_scores)),
            "valid_regime_rows": int(result.regime_scores["valid"].fillna(False).sum()),
            "regime_health_rows": int(len(result.regime_health)),
        }
    )


@app.command()
def inspect_regime(
    regime_id: str,
    db_path: str = "data/macro_engine.duckdb",
    limit: int = 10,
) -> None:
    """Inspect latest stored regime scores for one regime."""
    store = DuckDBStore(db_path)
    scores = store.read_regime_scores(regime_id).tail(limit)
    if scores.empty:
        console.print("[yellow]No regime score rows found[/yellow]")
        raise typer.Exit(code=1)
    console.print(scores.to_string(index=False))


@app.command()
def regime_health(db_path: str = "data/macro_engine.duckdb") -> None:
    """Show latest regime health rows from local storage."""
    store = DuckDBStore(db_path)
    table = store.read_table("regime_health")
    rich_table = Table(title="Regime Health")
    columns = [
        "date",
        "valid",
        "dominant_regime",
        "dominant_probability",
        "confidence",
        "valid_regime_count",
        "reason",
    ]
    for column in columns:
        rich_table.add_column(column)
    for row in table.sort_values("date").tail(30).to_dict(orient="records"):
        rich_table.add_row(*(str(row.get(column)) for column in columns))
    console.print(rich_table)


@app.command()
def current_regime(db_path: str = "data/macro_engine.duckdb") -> None:
    """Print the latest valid dominant regime as JSON."""
    store = DuckDBStore(db_path)
    health = store.read_table("regime_health")
    valid_health = health[health["valid"]].sort_values("date")
    if valid_health.empty:
        console.print_json(data={"valid": False, "reason": "no_valid_regime"})
        raise typer.Exit(code=1)
    latest = valid_health.iloc[-1]
    scores = store.read_regime_scores()
    latest_scores = scores[(scores["date"] == latest["date"]) & scores["probability"].notna()]
    probabilities = {
        row["regime_id"]: float(row["probability"])
        for row in latest_scores.sort_values("rank").to_dict(orient="records")
    }
    reported = _latest_reported_regime(store, latest)
    console.print_json(
        data={
            "date": str(latest["date"]),
            "dominant_regime": reported["reported_regime"],
            "probability": reported["reported_regime_probability"],
            "confidence": reported["reported_confidence"],
            "reported_regime": reported["reported_regime"],
            "reported_regime_probability": reported["reported_regime_probability"],
            "reported_confidence": reported["reported_confidence"],
            "raw_dominant_regime": latest["dominant_regime"],
            "raw_dominant_probability": float(latest["dominant_probability"]),
            "raw_confidence": float(latest["confidence"]),
            "regime_probabilities": probabilities,
            "transition_filter_applied": reported["transition_filter_applied"],
            "transition_filter_reason": reported["transition_filter_reason"],
            "valid": True,
        }
    )


@app.command()
def run_historical_diagnostic(
    config: Annotated[str, typer.Option("--config")] = "config/phase_b_sources.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
    parquet_dir: Annotated[str, typer.Option("--parquet-dir")] = "data/raw/fred",
) -> None:
    """Phase F: build revised-data historical diagnostic tables from regime outputs."""
    result = run_stored_historical_diagnostic(
        config_path=config,
        db_path=db_path,
        parquet_dir=parquet_dir,
    )
    console.print_json(data=result.summary)


@app.command()
def regime_timeline(db_path: str = "data/macro_engine.duckdb", limit: int = 20) -> None:
    """Show stored historical revised-data regime timeline rows."""
    store = DuckDBStore(db_path)
    table = store.read_table("historical_regime_timeline").sort_values("date").tail(limit)
    if table.empty:
        console.print("[yellow]No historical timeline rows found[/yellow]")
        raise typer.Exit(code=1)
    console.print(table.to_string(index=False))


@app.command()
def regime_transitions(db_path: str = "data/macro_engine.duckdb", limit: int = 20) -> None:
    """Show stored historical revised-data regime transition rows."""
    store = DuckDBStore(db_path)
    table = store.read_table("regime_transitions").sort_values("transition_date").tail(limit)
    if table.empty:
        console.print("[yellow]No regime transitions found[/yellow]")
        return
    console.print(table.to_string(index=False))


@app.command()
def diagnostic_summary(db_path: str = "data/macro_engine.duckdb") -> None:
    """Print stored historical revised-data diagnostic summary as JSON."""
    store = DuckDBStore(db_path)
    table = store.read_table("diagnostic_summary")
    if table.empty:
        console.print_json(data={"valid": False, "reason": "no_diagnostic_summary"})
        raise typer.Exit(code=1)
    console.print_json(json.dumps(table.iloc[-1].to_dict(), default=str))


@app.command()
def write_current_report(
    config: Annotated[str, typer.Option("--config")] = "config/phase_b_sources.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """Phase G: write current regime JSON and Markdown reports from stored outputs."""
    json_path, markdown_path = write_current_regime_report_service(
        config_path=config,
        db_path=db_path,
    )
    console.print_json(data={"json_path": str(json_path), "markdown_path": str(markdown_path)})


@app.command()
def write_diagnostic_report(
    config: Annotated[str, typer.Option("--config")] = "config/phase_b_sources.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """Phase G: write historical diagnostic JSON and Markdown reports from stored outputs."""
    json_path, markdown_path = write_historical_diagnostic_report_service(
        config_path=config,
        db_path=db_path,
    )
    console.print_json(data={"json_path": str(json_path), "markdown_path": str(markdown_path)})


@app.command()
def run_pipeline(
    config: Annotated[str, typer.Option("--config")] = "config/phase_b_sources.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
    parquet_dir: Annotated[str, typer.Option("--parquet-dir")] = "data/raw/fred",
    mode: Annotated[str, typer.Option("--mode")] = "live",
    start: Annotated[str | None, typer.Option("--start")] = None,
    end: Annotated[str | None, typer.Option("--end")] = None,
) -> None:
    """Phase H: orchestrate ingestion through reports using existing pipeline layers."""
    try:
        summary = run_full_pipeline(
            config_path=config,
            db_path=db_path,
            parquet_dir=parquet_dir,
            mode=mode,
            start=start,
            end=end,
        )
    except FredError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print_json(data=summary.to_dict())


@app.command("run-calibration-experiments")
def run_calibration_experiments_cli(
    experiment_config: Annotated[
        str,
        typer.Option("--experiment-config"),
    ] = "config/experiments/phase_l.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """Phase L: run calibration experiments without overwriting production outputs."""
    result = run_calibration_experiments(
        experiment_config_path=experiment_config,
        db_path=db_path,
    )
    console.print_json(
        data={
            "output_dir": str(result.output_dir),
            "variant_count": len(result.variant_results),
            "comparison_path": str(result.output_dir / "comparison.json"),
            "markdown_path": str(result.markdown_path),
        }
    )


@app.command("build-sector-scores")
def build_sector_scores_cli(
    config: Annotated[str, typer.Option("--config")] = "config/phase_b_sources.yaml",
    sector_config: Annotated[str, typer.Option("--sector-config")] = "config/sectors.yaml",
    exposure_config: Annotated[
        str,
        typer.Option("--exposure-config"),
    ] = "config/sector_exposures.yaml",
    prior_config: Annotated[
        str,
        typer.Option("--prior-config"),
    ] = "config/sector_regime_priors.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
    parquet_dir: Annotated[str, typer.Option("--parquet-dir")] = "data/raw/fred",
) -> None:
    """v0.2: build sector macro attractiveness scores from stored macro outputs."""
    result = build_stored_sector_scores(
        config_path=config,
        sector_config_path=sector_config,
        exposure_config_path=exposure_config,
        prior_config_path=prior_config,
        db_path=db_path,
        parquet_dir=parquet_dir,
    )
    console.print_json(
        data={
            "sector_score_rows": int(len(result.sector_scores)),
            "valid_sector_score_rows": int(result.sector_scores["valid"].fillna(False).sum()),
            "component_rows": int(len(result.components)),
            "sector_health_rows": int(len(result.sector_health)),
        }
    )


@app.command("current-sector-ranking")
def current_sector_ranking(
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """Print latest valid sector ranking as JSON."""
    store = DuckDBStore(db_path)
    scores = store.read_table("sector_scores")
    valid_scores = scores[scores["valid"]].sort_values(["date", "rank"])
    if valid_scores.empty:
        console.print_json(data={"valid": False, "reason": "no_valid_sector_scores"})
        raise typer.Exit(code=1)
    latest_date = valid_scores["date"].max()
    latest = valid_scores[valid_scores["date"] == latest_date].sort_values("rank")
    console.print_json(
        data={
            "valid": True,
            "date": str(latest_date),
            "reported_macro_regime": latest.iloc[0]["macro_reported_regime"],
            "raw_macro_leader": latest.iloc[0]["macro_raw_dominant_regime"],
            "macro_confidence": float(latest.iloc[0]["macro_confidence"]),
            "ranking": [
                {
                    "rank": int(row["rank"]),
                    "sector_id": row["sector_id"],
                    "raw_sector_score": float(row["raw_sector_score"]),
                    "confidence_adjusted_score": float(row["confidence_adjusted_score"]),
                }
                for row in latest.to_dict(orient="records")
            ],
        }
    )


@app.command("inspect-sector")
def inspect_sector(
    sector_id: str,
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
    limit: Annotated[int, typer.Option("--limit")] = 20,
) -> None:
    """Inspect latest stored sector score and component rows for one sector."""
    store = DuckDBStore(db_path)
    scores = store.read_sector_scores(sector_id)
    if scores.empty:
        console.print("[yellow]No sector score rows found[/yellow]")
        raise typer.Exit(code=1)
    latest_score = scores.sort_values("date").tail(1)
    latest_date = latest_score.iloc[-1]["date"]
    components = store.read_sector_components(sector_id)
    latest_components = components[components["date"] == latest_date].tail(limit)
    console.print(f"[bold]{sector_id} latest score[/bold]")
    console.print(latest_score.to_string(index=False))
    console.print(f"[bold]{sector_id} latest components[/bold]")
    console.print(latest_components.to_string(index=False))


@app.command("sector-health")
def sector_health(
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """Show latest sector health rows from local storage."""
    store = DuckDBStore(db_path)
    table = store.read_table("sector_health")
    if table.empty:
        console.print("[yellow]No sector health rows found[/yellow]")
        raise typer.Exit(code=1)
    latest_date = table["date"].max()
    latest = table[table["date"] == latest_date].sort_values("sector_id")
    rich_table = Table(title="Sector Health")
    columns = ["sector_id", "date", "valid", "component_count", "warning_count", "reason"]
    for column in columns:
        rich_table.add_column(column)
    for row in latest.to_dict(orient="records"):
        rich_table.add_row(*(str(row.get(column)) for column in columns))
    console.print(rich_table)


@app.command("write-sector-report")
def write_sector_report(
    config: Annotated[str, typer.Option("--config")] = "config/phase_b_sources.yaml",
    sector_config: Annotated[str, typer.Option("--sector-config")] = "config/sectors.yaml",
    exposure_config: Annotated[
        str,
        typer.Option("--exposure-config"),
    ] = "config/sector_exposures.yaml",
    prior_config: Annotated[
        str,
        typer.Option("--prior-config"),
    ] = "config/sector_regime_priors.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """v0.2: write current sector ranking JSON and Markdown reports."""
    json_path, markdown_path = write_current_sector_report(
        config_path=config,
        sector_config_path=sector_config,
        exposure_config_path=exposure_config,
        prior_config_path=prior_config,
        db_path=db_path,
    )
    console.print_json(data={"json_path": str(json_path), "markdown_path": str(markdown_path)})


@app.command("ingest-sector-proxy-prices")
def ingest_sector_proxy_prices_cli(
    config: Annotated[str, typer.Option("--config")] = "config/sector_validation.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """v0.2-F: ingest local sector ETF proxy prices for diagnostic validation."""
    try:
        prices = ingest_sector_proxy_prices(config_path=config, db_path=db_path)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print_json(
        data={
            "price_rows": int(len(prices)),
            "tickers": sorted(prices["ticker"].unique().tolist()) if not prices.empty else [],
        }
    )


@app.command("run-sector-validation")
def run_sector_validation_cli(
    config: Annotated[str, typer.Option("--config")] = "config/sector_validation.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """v0.2-F: compare stored sector scores with future sector ETF relative returns."""
    result = run_stored_sector_validation(config_path=config, db_path=db_path)
    console.print_json(
        data={
            "return_rows": int(len(result.returns)),
            "valid_return_rows": int(result.returns["valid"].fillna(False).sum())
            if not result.returns.empty
            else 0,
            "summary_rows": int(len(result.summary)),
        }
    )


@app.command("sector-validation-summary")
def sector_validation_summary(
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """Show stored sector ETF proxy validation summary."""
    store = DuckDBStore(db_path)
    table = store.read_table("sector_validation_summary")
    if table.empty:
        console.print_json(data={"valid": False, "reason": "no_sector_validation_summary"})
        raise typer.Exit(code=1)
    console.print_json(data=_json_ready({"valid": True, "summary": table.to_dict(orient="records")}))


@app.command("write-sector-validation-report")
def write_sector_validation_report_cli(
    config: Annotated[str, typer.Option("--config")] = "config/sector_validation.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """v0.2-F: write sector ETF proxy validation JSON and Markdown reports."""
    json_path, markdown_path = write_sector_validation_report(
        config_path=config,
        db_path=db_path,
    )
    console.print_json(data={"json_path": str(json_path), "markdown_path": str(markdown_path)})


def _latest_reported_regime(store: DuckDBStore, latest_raw) -> dict:
    try:
        timeline = store.read_table("historical_regime_timeline")
    except Exception:
        timeline = pd.DataFrame()
    if timeline.empty:
        return {
            "reported_regime": latest_raw["dominant_regime"],
            "reported_regime_probability": float(latest_raw["dominant_probability"]),
            "reported_confidence": float(latest_raw["confidence"]),
            "transition_filter_applied": False,
            "transition_filter_reason": "no_timeline",
        }
    latest_date = pd.Timestamp(latest_raw["date"])
    row = timeline[
        (pd.to_datetime(timeline["date"], errors="coerce") == latest_date) & (timeline["valid"])
    ]
    if row.empty:
        row = timeline[timeline["valid"]].sort_values("date").tail(1)
    if row.empty:
        return {
            "reported_regime": latest_raw["dominant_regime"],
            "reported_regime_probability": float(latest_raw["dominant_probability"]),
            "reported_confidence": float(latest_raw["confidence"]),
            "transition_filter_applied": False,
            "transition_filter_reason": "no_valid_timeline",
        }
    latest_reported = row.iloc[-1]
    reported_regime = latest_reported.get("reported_regime") or latest_reported.get(
        "dominant_regime"
    )
    reported_probability = latest_reported.get("reported_regime_probability")
    if pd.isna(reported_probability):
        reported_probability = latest_reported.get("dominant_probability")
    reported_confidence = latest_reported.get("reported_confidence")
    if pd.isna(reported_confidence):
        reported_confidence = latest_reported.get("confidence")
    return {
        "reported_regime": reported_regime,
        "reported_regime_probability": None
        if pd.isna(reported_probability)
        else float(reported_probability),
        "reported_confidence": None if pd.isna(reported_confidence) else float(reported_confidence),
        "transition_filter_applied": bool(
            latest_reported.get("transition_filter_applied", False)
        ),
        "transition_filter_reason": latest_reported.get("transition_filter_reason", "unknown"),
    }


def _json_ready(value):
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if value is None or pd.isna(value):
        return None
    return value


if __name__ == "__main__":
    app()
