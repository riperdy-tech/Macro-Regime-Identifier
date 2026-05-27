from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from macro_engine.cli import app
from macro_engine.daily_health import daily_health_check
from macro_engine.news.config import load_news_source_watchlist_config, load_news_sources_config
from macro_engine.news.ingest import load_rss_source
from macro_engine.news.ingest import validate_news_input_config
from macro_engine.news.source_coverage import (
    build_news_source_coverage_report,
    write_news_source_coverage_report,
)
from macro_engine.news.service import classify_stored_news
from macro_engine.storage.duckdb_store import DuckDBStore


runner = CliRunner()


def test_news_source_profiles_and_watchlist_load():
    sources = load_news_sources_config("config/news_sources.yaml")
    assert any("daily_local_csv" in source.profiles for source in sources.news_sources)
    assert any(source.provider == "rss" for source in sources.news_sources)

    watchlist = load_news_source_watchlist_config("config/news_source_watchlist.yaml")
    enabled_groups = {
        source.source_group for source in watchlist.news_source_watchlist if source.enabled
    }
    assert "macro_general" in enabled_groups
    assert "labor" in enabled_groups
    assert len(enabled_groups) >= 12


def test_rss_source_parsing_with_mocked_feed(monkeypatch):
    source = next(
        source
        for source in load_news_sources_config("config/news_sources.yaml").news_sources
        if source.provider == "rss"
    )
    source.lookback_days = 0
    feed = b"""<?xml version="1.0"?>
<rss><channel>
  <item>
    <title>Rates pressure builds</title>
    <description>Central bank officials discussed restrictive policy.</description>
    <link>https://example.invalid/rates</link>
    <pubDate>Tue, 05 May 2026 12:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return feed

    monkeypatch.setattr("macro_engine.news.ingest.urlopen", lambda *_args, **_kwargs: Response())

    items = load_rss_source(source)
    assert len(items) == 1
    assert items[0].provider == "rss"
    assert items[0].raw_metadata["source_group"] == "macro_general"


def test_atom_source_parsing_with_namespaced_fields(monkeypatch):
    source = next(
        source
        for source in load_news_sources_config("config/news_sources.yaml").news_sources
        if source.source_id == "ai_compute_nvidia_developer_rss"
    )
    source.lookback_days = 0
    feed = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>GPU inference throughput improves</title>
    <summary>Developers described lower latency inference serving for AI workloads.</summary>
    <link href="https://developer.nvidia.com/blog/example"/>
    <updated>2026-05-05T12:00:00Z</updated>
  </entry>
</feed>
"""

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return feed

    monkeypatch.setattr("macro_engine.news.ingest.urlopen", lambda *_args, **_kwargs: Response())

    items = load_rss_source(source)
    assert len(items) == 1
    assert items[0].provider == "rss"
    assert items[0].source == "nvidia_developer_blog"
    assert items[0].raw_metadata["source_group"] == "ai_compute"


def test_ai_compute_rss_sources_are_disabled_by_default_and_use_real_xml_feeds():
    sources = load_news_sources_config("config/news_sources.yaml")
    ai_compute_sources = [
        source for source in sources.news_sources if "ai_compute_rss" in source.profiles
    ]
    assert len(ai_compute_sources) == 3
    assert all(source.enabled is False for source in ai_compute_sources)
    assert all(source.source_group == "ai_compute" for source in ai_compute_sources)
    assert all("example.invalid" not in str(source.feed_url) for source in ai_compute_sources)
    assert all("cloud.google.com/blog/products/rss" not in str(source.feed_url) for source in ai_compute_sources)


def test_local_news_source_group_mapping_rules(tmp_path: Path):
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "\n".join(
            [
                "title,body,source,source_url,published_at",
                (
                    "Weekly jobless claims rise,"
                    "A labor-market article with enough body text for validation,"
                    "Example News,https://example.invalid/jobs,2026-05-18T12:00:00Z"
                ),
                (
                    "Mortgage rates rise,"
                    "A real-estate article with enough body text for validation,"
                    "Example News,https://example.invalid/housing,2026-05-18T13:00:00Z"
                ),
            ]
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "news_sources.yaml"
    config_path.write_text(
        f"""
news_sources:
  - source_id: mapped_local_csv
    provider: local_csv
    enabled: true
    path: {csv_path.as_posix()}
source_group_rules:
  - rule_id: labor_rule
    source_group: labor
    source_ids: [mapped_local_csv]
    title_keywords: [jobless]
  - rule_id: real_estate_rule
    source_group: real_estate
    source_ids: [mapped_local_csv]
    title_keywords: [mortgage]
""",
        encoding="utf-8",
    )

    summary = validate_news_input_config(config_path)

    assert summary["item_count_by_source_group"] == {"labor": 1, "real_estate": 1}
    assert summary["source_group_count"] == 2
    assert summary["unmapped_item_count"] == 0


def test_explicit_source_group_and_query_group_win_before_rules(tmp_path: Path):
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        "\n".join(
            [
                "title,body,source,source_url,published_at,source_group,query_group",
                (
                    "Mortgage rates rise,"
                    "A housing article with explicit group metadata,"
                    "Example News,https://example.invalid/housing,2026-05-18T12:00:00Z,"
                    "consumer,real_estate"
                ),
                (
                    "Generic topic,"
                    "A generic article with query group metadata,"
                    "Example News,https://example.invalid/generic,2026-05-18T13:00:00Z,"
                    ",labor"
                ),
            ]
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "news_sources.yaml"
    config_path.write_text(
        f"""
news_sources:
  - source_id: explicit_local_csv
    provider: local_csv
    enabled: true
    path: {csv_path.as_posix()}
source_group_rules:
  - rule_id: real_estate_rule
    source_group: real_estate
    source_ids: [explicit_local_csv]
    title_keywords: [mortgage]
""",
        encoding="utf-8",
    )

    summary = validate_news_input_config(config_path)

    assert summary["item_count_by_source_group"] == {"consumer": 1, "labor": 1}
    assert summary["unmapped_pct"] == 0.0


def test_source_coverage_report_and_cli(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    store.upsert_news_items(_news_items())

    payload = build_news_source_coverage_report(
        config_path="config/news_source_watchlist.yaml",
        db_path=db_path,
    )
    assert payload["valid"] is True
    assert payload["item_count_by_group"]["macro_general"] == 2
    assert payload["unmapped_item_count"] == 0
    assert payload["source_group_count"] == 1
    assert "labor" in payload["missing_data_groups"]

    json_path, markdown_path = write_news_source_coverage_report(
        config_path="config/news_source_watchlist.yaml",
        db_path=db_path,
    )
    assert json.loads(json_path.read_text(encoding="utf-8"))["valid"] is True
    markdown = markdown_path.read_text(encoding="utf-8").lower()
    assert "news source coverage report" in markdown
    assert not any(
        term in markdown
        for term in [
            "buy",
            "sell",
            "overweight",
            "underweight",
            "avoid",
            "recommendation",
            "recommend",
            "trade",
            "position sizing",
            "portfolio allocation",
        ]
    )

    validate_cli = runner.invoke(app, ["validate-news-sources"])
    assert validate_cli.exit_code == 0, validate_cli.output
    coverage_cli = runner.invoke(app, ["news-source-coverage", "--db-path", str(db_path)])
    assert coverage_cli.exit_code == 0, coverage_cli.output
    write_cli = runner.invoke(
        app,
        ["write-news-source-coverage-report", "--db-path", str(db_path)],
    )
    assert write_cli.exit_code == 0, write_cli.output


def test_daily_health_check_and_cli(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    result = daily_health_check(config_path="config/daily_pipeline.yaml", db_path=db_path)
    assert result["valid"] is True
    assert result["status"] in {"ok", "warning"}
    assert any(check["name"] == "database" for check in result["checks"])

    cli = runner.invoke(app, ["daily-health-check", "--db-path", str(db_path)])
    assert cli.exit_code == 0, cli.output
    assert '"valid": true' in cli.output


def test_classify_news_limit_only_unclassified_and_incremental_storage(tmp_path: Path):
    db_path = tmp_path / "macro.duckdb"
    store = DuckDBStore(db_path)
    store.initialize()
    store.upsert_news_items(_news_items())

    result = classify_stored_news(
        ai_config_path="config/news_ai.yaml",
        themes_config_path="config/news_themes.yaml",
        db_path=db_path,
        limit=1,
        only_unclassified=True,
        progress=True,
    )
    stored = store.read_table("news_classifications")
    assert len(result["classifications"]) == 1
    assert len(stored) == 1

    second = classify_stored_news(
        ai_config_path="config/news_ai.yaml",
        themes_config_path="config/news_themes.yaml",
        db_path=db_path,
        limit=5,
        only_unclassified=True,
        progress=False,
    )
    stored_second = store.read_table("news_classifications")
    assert len(stored_second) == 2
    assert len(second["classifications"]) == 2

    cli = runner.invoke(
        app,
        [
            "classify-news",
            "--config",
            "config/news_ai.yaml",
            "--db-path",
            str(db_path),
            "--max-items",
            "1",
            "--only-unclassified",
            "--no-progress",
        ],
    )
    assert cli.exit_code == 0, cli.output
    assert '"classification_rows": 2' in cli.output


def test_scheduled_run_artifacts_exist():
    ps1 = Path("scripts/run_daily_diagnostic.ps1")
    sh = Path("scripts/run_daily_diagnostic.sh")
    runbook = Path("docs/operations/daily_runbook.md")
    assert ps1.exists()
    assert sh.exists()
    assert runbook.exists()
    assert "run-daily-diagnostic" in ps1.read_text(encoding="utf-8")
    assert "logs/daily" in runbook.read_text(encoding="utf-8")


def _news_items() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "news_id": "news_1",
                "source": "synthetic_newswire",
                "source_url": "https://example.invalid/1",
                "title": "Macro event one",
                "body": "A broad macro event affected the diagnostic backdrop.",
                "published_at": "2026-05-18T12:00:00Z",
                "ingested_at": "2026-05-18T12:01:00Z",
                "provider": "local_csv",
                "raw_metadata": {"source_group": "macro_general"},
                "content_hash": "hash_1",
            },
            {
                "news_id": "news_2",
                "source": "synthetic_newswire",
                "source_url": "https://example.invalid/2",
                "title": "Macro event two",
                "body": "A second macro event affected the diagnostic backdrop.",
                "published_at": "2026-05-18T13:00:00Z",
                "ingested_at": "2026-05-18T13:01:00Z",
                "provider": "local_csv",
                "raw_metadata": {"source_group": "macro_general"},
                "content_hash": "hash_2",
            },
        ]
    )
