from __future__ import annotations

from pathlib import Path
import json
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from macro_engine.config.loader import load_all_configs
from macro_engine.ingest.fred import FredError
from macro_engine.ingest.service import run_fred_ingestion
from macro_engine.outputs.json_writer import write_json
from macro_engine.outputs.report import build_markdown_report, write_markdown_report
from macro_engine.pipeline import classify_observations
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


if __name__ == "__main__":
    app()
