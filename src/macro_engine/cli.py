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
from macro_engine.news.report import write_news_report as write_news_report_service
from macro_engine.news.combined import build_stored_combined_sector_diagnostics
from macro_engine.news.combined_report import write_combined_sector_report
from macro_engine.news.score_report import write_news_score_report as write_news_score_report_service
from macro_engine.news.scoring import build_stored_news_scores
from macro_engine.news.ingest import validate_news_input_config
from macro_engine.news.service import classify_stored_news, ingest_stored_news
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
from macro_engine.sectors.calibration import run_sector_calibration_experiments
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
    except (FileNotFoundError, ValueError) as exc:
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


@app.command("run-sector-calibration-experiments")
def run_sector_calibration_experiments_cli(
    experiment_config: Annotated[
        str,
        typer.Option("--experiment-config"),
    ] = "config/experiments/sector_calibration_v02_m1.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """v0.2-M1: run sector calibration experiments without mutating production configs."""
    result = run_sector_calibration_experiments(
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


@app.command("ingest-news")
def ingest_news(
    config: Annotated[str, typer.Option("--config")] = "config/news_sources.yaml",
    profile: Annotated[str | None, typer.Option("--profile")] = None,
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """v0.3-M1: ingest local/manual news items into storage."""
    frame = ingest_stored_news(config_path=config, db_path=db_path, profile=profile)
    console.print_json(
        data={
            "news_rows": int(len(frame)),
            "sources": sorted(frame["source"].unique().tolist()) if not frame.empty else [],
        }
    )


@app.command("validate-news-input")
def validate_news_input(
    config: Annotated[str, typer.Option("--config")] = "config/news_sources.yaml",
    profile: Annotated[str | None, typer.Option("--profile")] = None,
) -> None:
    """v0.4-M1: validate local news input files before ingestion."""
    try:
        summary = validate_news_input_config(config_path=config, profile=profile)
    except (FileNotFoundError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print_json(data=summary)


@app.command("classify-news")
def classify_news(
    config: Annotated[str, typer.Option("--config")] = "config/news_ai.yaml",
    themes_config: Annotated[str, typer.Option("--themes-config")] = "config/news_themes.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
    limit: Annotated[int | None, typer.Option("--limit")] = None,
) -> None:
    """v0.3-M1: classify stored news using mock or configured AI provider."""
    result = classify_stored_news(
        ai_config_path=config,
        themes_config_path=themes_config,
        db_path=db_path,
        limit=limit,
    )
    classifications = result["classifications"]
    console.print_json(
        data={
            "classification_rows": int(len(classifications)),
            "successful_classifications": int(
                (classifications["classification_status"] == "success").sum()
            )
            if not classifications.empty
            else 0,
            "theme_score_rows": int(len(result["theme_scores"])),
            "sector_impact_rows": int(len(result["sector_impacts"])),
        }
    )


@app.command("inspect-news-item")
def inspect_news_item(
    news_id: str,
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """Inspect one stored news item and any classification rows."""
    store = DuckDBStore(db_path)
    items = store.read_news_items(news_id)
    if items.empty:
        console.print("[yellow]No news item found[/yellow]")
        raise typer.Exit(code=1)
    classifications = store.read_table("news_classifications")
    classifications = classifications[classifications["news_id"] == news_id]
    console.print("[bold]News item[/bold]")
    console.print(items.to_string(index=False))
    console.print("[bold]Classifications[/bold]")
    if classifications.empty:
        console.print("[yellow]No classification rows found[/yellow]")
    else:
        console.print(classifications.to_string(index=False))


@app.command("news-classification-summary")
def news_classification_summary(
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """Show stored news classification summary as JSON."""
    store = DuckDBStore(db_path)
    classifications = store.read_table("news_classifications")
    theme_scores = store.read_table("news_theme_scores")
    sector_impacts = store.read_table("news_sector_impacts")
    if classifications.empty:
        console.print_json(data={"valid": False, "reason": "no_news_classifications"})
        raise typer.Exit(code=1)
    console.print_json(
        data={
            "valid": True,
            "classification_count": int(len(classifications)),
            "successful_classification_count": int(
                (classifications["classification_status"] == "success").sum()
            ),
            "top_themes": theme_scores["theme_id"].value_counts().head(10).to_dict()
            if not theme_scores.empty
            else {},
            "top_sectors": sector_impacts["sector_id"].value_counts().head(10).to_dict()
            if not sector_impacts.empty
            else {},
        }
    )


@app.command("write-news-report")
def write_news_report(
    config: Annotated[str, typer.Option("--config")] = "config/news_ai.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """v0.3-M1: write news classification JSON and Markdown reports."""
    json_path, markdown_path = write_news_report_service(ai_config_path=config, db_path=db_path)
    console.print_json(data={"json_path": str(json_path), "markdown_path": str(markdown_path)})


@app.command("build-news-scores")
def build_news_scores_cli(
    config: Annotated[str, typer.Option("--config")] = "config/news_scoring.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """v0.3-M2: aggregate stored news classifications into theme and sector scores."""
    result = build_stored_news_scores(config_path=config, db_path=db_path)
    console.print_json(
        data={
            "daily_theme_rows": int(len(result.daily_theme_scores)),
            "daily_sector_rows": int(len(result.daily_sector_scores)),
            "weekly_theme_rows": int(len(result.weekly_theme_scores)),
            "weekly_sector_rows": int(len(result.weekly_sector_scores)),
            "component_rows": int(len(result.components)),
            "status": result.runs.iloc[-1]["status"] if not result.runs.empty else "unknown",
        }
    )


@app.command("current-news-summary")
def current_news_summary(
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """Print latest aggregated news score summary as JSON."""
    store = DuckDBStore(db_path)
    themes = store.read_table("news_daily_theme_scores")
    sectors = store.read_table("news_daily_sector_scores")
    if themes.empty and sectors.empty:
        console.print_json(data={"valid": False, "reason": "no_news_scores"})
        raise typer.Exit(code=1)
    latest_date = _latest_news_score_date(themes, sectors)
    latest_themes = _rows_for_date(themes, "score_date", latest_date)
    latest_sectors = _rows_for_date(sectors, "score_date", latest_date)
    console.print_json(
        data={
            "valid": True,
            "date": str(latest_date.date()),
            "top_positive_themes": _rank_news_rows(
                _filter_score_sign(latest_themes, "adjusted_score", positive=True),
                id_column="theme_id",
                score_column="adjusted_score",
                ascending=False,
            ),
            "top_sector_tailwinds": _rank_news_rows(
                _filter_score_sign(latest_sectors, "adjusted_news_score", positive=True),
                id_column="sector_id",
                score_column="adjusted_news_score",
                ascending=False,
            ),
            "top_sector_headwinds": _rank_news_rows(
                _filter_score_sign(latest_sectors, "adjusted_news_score", positive=False),
                id_column="sector_id",
                score_column="adjusted_news_score",
                ascending=True,
            ),
        }
    )


@app.command("inspect-news-score")
def inspect_news_score(
    date: Annotated[str | None, typer.Option("--date")] = None,
    sector: Annotated[str | None, typer.Option("--sector")] = None,
    theme: Annotated[str | None, typer.Option("--theme")] = None,
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """Inspect aggregated news scores and components by date, sector, or theme."""
    store = DuckDBStore(db_path)
    components = store.read_table("news_score_components")
    if components.empty:
        console.print("[yellow]No news score components found[/yellow]")
        raise typer.Exit(code=1)
    frame = components.copy()
    frame["score_date"] = pd.to_datetime(frame["score_date"], errors="coerce")
    if date:
        frame = frame[frame["score_date"] == pd.Timestamp(date)]
    if sector:
        frame = frame[frame["sector_id"] == sector]
    if theme:
        frame = frame[frame["theme_id"] == theme]
    if frame.empty:
        console.print("[yellow]No matching news score rows found[/yellow]")
        raise typer.Exit(code=1)
    console.print(frame.sort_values(["score_date", "component_type", "news_id"]).to_string(index=False))


@app.command("write-news-score-report")
def write_news_score_report(
    config: Annotated[str, typer.Option("--config")] = "config/news_scoring.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """v0.3-M2: write aggregated news score JSON and Markdown reports."""
    json_path, markdown_path = write_news_score_report_service(config_path=config, db_path=db_path)
    console.print_json(data={"json_path": str(json_path), "markdown_path": str(markdown_path)})


@app.command("build-combined-sector-diagnostics")
def build_combined_sector_diagnostics_cli(
    config: Annotated[str, typer.Option("--config")] = "config/sector_news_integration.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """v0.3-M3: build experimental combined macro-sector plus news diagnostics."""
    result = build_stored_combined_sector_diagnostics(config_path=config, db_path=db_path)
    console.print_json(
        data={
            "combined_rows": int(len(result.diagnostics)),
            "component_rows": int(len(result.components)),
        }
    )


@app.command("current-combined-sector-ranking")
def current_combined_sector_ranking(
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """Print latest experimental combined sector diagnostic ranking as JSON."""
    store = DuckDBStore(db_path)
    diagnostics = store.read_table("combined_sector_diagnostics")
    if diagnostics.empty:
        console.print_json(data={"valid": False, "reason": "no_combined_sector_diagnostics"})
        raise typer.Exit(code=1)
    frame = diagnostics.copy()
    frame["diagnostic_date"] = pd.to_datetime(frame["diagnostic_date"], errors="coerce")
    latest_date = frame["diagnostic_date"].max()
    latest = frame[frame["diagnostic_date"] == latest_date].sort_values("rank")
    console.print_json(
        data={
            "valid": True,
            "date": str(latest_date.date()),
            "ranking": [
                {
                    "rank": int(row["rank"]),
                    "sector_id": row["sector_id"],
                    "combined_score": float(row["combined_score"]),
                    "sector_macro_score": float(row["sector_macro_score"]),
                    "sector_news_score": float(row["sector_news_score"]),
                    "news_item_count": int(row["news_item_count"]),
                }
                for row in latest.to_dict(orient="records")
            ],
        }
    )


@app.command("inspect-combined-sector")
def inspect_combined_sector(
    sector_id: str,
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """Inspect latest combined sector diagnostic row and components for one sector."""
    store = DuckDBStore(db_path)
    diagnostics = store.read_table("combined_sector_diagnostics")
    if diagnostics.empty:
        console.print("[yellow]No combined sector diagnostics found[/yellow]")
        raise typer.Exit(code=1)
    frame = diagnostics[diagnostics["sector_id"] == sector_id].copy()
    if frame.empty:
        console.print("[yellow]No combined diagnostic found for sector[/yellow]")
        raise typer.Exit(code=1)
    frame["diagnostic_date"] = pd.to_datetime(frame["diagnostic_date"], errors="coerce")
    latest = frame.sort_values("diagnostic_date").tail(1)
    latest_date = latest.iloc[-1]["diagnostic_date"]
    components = store.read_table("combined_sector_diagnostic_components")
    components["diagnostic_date"] = pd.to_datetime(components["diagnostic_date"], errors="coerce")
    latest_components = components[
        (components["sector_id"] == sector_id)
        & (components["diagnostic_date"] == latest_date)
    ]
    console.print(f"[bold]{sector_id} latest combined diagnostic[/bold]")
    console.print(latest.to_string(index=False))
    console.print(f"[bold]{sector_id} components[/bold]")
    console.print(latest_components.to_string(index=False))


@app.command("write-combined-sector-report")
def write_combined_sector_report_cli(
    config: Annotated[str, typer.Option("--config")] = "config/sector_news_integration.yaml",
    db_path: Annotated[str, typer.Option("--db-path")] = "data/macro_engine.duckdb",
) -> None:
    """v0.3-M3: write experimental combined sector diagnostic reports."""
    json_path, markdown_path = write_combined_sector_report(config_path=config, db_path=db_path)
    console.print_json(data={"json_path": str(json_path), "markdown_path": str(markdown_path)})


def _latest_news_score_date(themes: pd.DataFrame, sectors: pd.DataFrame) -> pd.Timestamp:
    dates = []
    if not themes.empty:
        dates.append(pd.to_datetime(themes["score_date"], errors="coerce").max())
    if not sectors.empty:
        dates.append(pd.to_datetime(sectors["score_date"], errors="coerce").max())
    return max(date for date in dates if pd.notna(date))


def _rows_for_date(frame: pd.DataFrame, column: str, date: pd.Timestamp) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    result = frame.copy()
    result[column] = pd.to_datetime(result[column], errors="coerce")
    return result[result[column] == date]


def _rank_news_rows(
    frame: pd.DataFrame,
    *,
    id_column: str,
    score_column: str,
    ascending: bool,
) -> list[dict]:
    if frame.empty:
        return []
    ranked = frame.sort_values([score_column, id_column], ascending=[ascending, True]).head(5)
    return [
        {
            "id": row[id_column],
            "score": float(row[score_column]),
            "item_count": _news_row_item_count(row),
            "avg_confidence": None
            if pd.isna(row.get("avg_confidence"))
            else float(row.get("avg_confidence")),
        }
        for row in ranked.to_dict(orient="records")
    ]


def _filter_score_sign(frame: pd.DataFrame, column: str, *, positive: bool) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame[frame[column] > 0].copy() if positive else frame[frame[column] < 0].copy()


def _news_row_item_count(row: dict) -> int:
    if "item_count" in row and not pd.isna(row.get("item_count")):
        return int(row["item_count"])
    return int(
        (row.get("positive_item_count") or 0)
        + (row.get("negative_item_count") or 0)
        + (row.get("neutral_item_count") or 0)
    )


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
