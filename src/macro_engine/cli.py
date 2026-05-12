from __future__ import annotations

from pathlib import Path
import json
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from macro_engine.config.loader import load_all_configs
from macro_engine.dimensions.service import build_stored_dimensions
from macro_engine.features.service import build_stored_features
from macro_engine.ingest.fred import FredError
from macro_engine.ingest.service import run_fred_ingestion
from macro_engine.outputs.json_writer import write_json
from macro_engine.outputs.report import build_markdown_report, write_markdown_report
from macro_engine.pipeline import classify_observations
from macro_engine.regimes.service import build_stored_regimes
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
    console.print_json(
        data={
            "date": str(latest["date"]),
            "dominant_regime": latest["dominant_regime"],
            "probability": float(latest["dominant_probability"]),
            "confidence": float(latest["confidence"]),
            "regime_probabilities": probabilities,
            "valid": True,
        }
    )


if __name__ == "__main__":
    app()
