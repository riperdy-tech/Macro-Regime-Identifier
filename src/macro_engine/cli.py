from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from macro_engine.config.loader import load_all_configs
from macro_engine.outputs.json_writer import write_json
from macro_engine.outputs.report import build_markdown_report, write_markdown_report
from macro_engine.pipeline import classify_observations
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


if __name__ == "__main__":
    app()
