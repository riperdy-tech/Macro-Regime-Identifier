from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
import yaml

from macro_engine.news.schema import (
    ALLOWED_IMPACT_DIRECTIONS,
    ALLOWED_THEME_DIRECTIONS,
    ALLOWED_TIME_HORIZONS,
)


REQUIRED_NEWS_SOURCE_GROUPS = {
    "macro_general",
    "inflation_rates",
    "labor",
    "energy_commodities",
    "credit_financial_conditions",
    "real_estate",
    "consumer",
    "manufacturing_industrials",
    "geopolitical",
    "technology_ai",
    "healthcare",
    "defensive_sectors",
    "ai_compute",
}


class NewsSourceDefinition(BaseModel):
    source_id: str
    provider: Literal["local_csv", "local_json", "manual_text", "rss"]
    enabled: bool = True
    profiles: list[str] = Field(default_factory=list)
    path: str | None = None
    feed_url: str | None = None
    source: str | None = None
    source_group: str | None = None
    max_items: int = Field(default=50, ge=1)
    lookback_days: int = Field(default=7, ge=0)
    items: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_provider_fields(self):
        if self.provider in {"local_csv", "local_json"} and not self.path:
            raise ValueError(f"{self.provider} source {self.source_id} requires path")
        if self.provider == "manual_text" and not self.items:
            raise ValueError(f"manual_text source {self.source_id} requires items")
        if self.provider == "rss" and not self.feed_url:
            raise ValueError(f"rss source {self.source_id} requires feed_url")
        if self.source_group and self.source_group not in REQUIRED_NEWS_SOURCE_GROUPS:
            raise ValueError(f"unknown source_group {self.source_group}")
        return self


class NewsSourceGroupRule(BaseModel):
    rule_id: str
    source_group: str
    enabled: bool = True
    source_ids: list[str] = Field(default_factory=list)
    source_keywords: list[str] = Field(default_factory=list)
    title_keywords: list[str] = Field(default_factory=list)
    body_keywords: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_rule(self):
        if self.source_group not in REQUIRED_NEWS_SOURCE_GROUPS:
            raise ValueError(f"unknown source_group {self.source_group}")
        if not any(
            [self.source_ids, self.source_keywords, self.title_keywords, self.body_keywords]
        ):
            raise ValueError(f"source group rule {self.rule_id} needs at least one matcher")
        return self


class NewsSourcesConfig(BaseModel):
    news_sources: list[NewsSourceDefinition]
    source_group_rules: list[NewsSourceGroupRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source_group_rules(self):
        rule_ids = [rule.rule_id for rule in self.source_group_rules]
        duplicates = {rule_id for rule_id in rule_ids if rule_ids.count(rule_id) > 1}
        if duplicates:
            raise ValueError(f"duplicate source group rules: {sorted(duplicates)}")
        return self


class NewsSourceWatchlistEntry(BaseModel):
    source_id: str
    source_name: str
    source_group: str
    provider_type: Literal["local_csv", "local_json", "rss"]
    enabled: bool = True
    feed_url: str | None = None
    path: str | None = None
    max_items_per_run: int = Field(default=25, ge=1)
    lookback_days: int = Field(default=7, ge=0)
    region: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_entry(self):
        if self.source_group not in REQUIRED_NEWS_SOURCE_GROUPS:
            raise ValueError(f"unknown source_group {self.source_group}")
        if self.provider_type in {"local_csv", "local_json"} and not self.path:
            raise ValueError(f"{self.provider_type} watchlist entry {self.source_id} requires path")
        if self.provider_type == "rss" and not self.feed_url:
            raise ValueError(f"rss watchlist entry {self.source_id} requires feed_url")
        return self


class NewsSourceCoverageConfig(BaseModel):
    output_dir: str = "outputs"
    required_source_groups: list[str] = Field(
        default_factory=lambda: sorted(REQUIRED_NEWS_SOURCE_GROUPS)
    )
    max_group_share: float = Field(default=0.35, ge=0.0, le=1.0)
    max_unmapped_pct: float = Field(default=0.20, ge=0.0, le=1.0)
    min_source_groups: int = Field(default=3, ge=0)
    max_single_group_pct: float = Field(default=0.50, ge=0.0, le=1.0)
    max_old_item_pct: float = Field(default=0.20, ge=0.0, le=1.0)
    stale_after_days: int = Field(default=3, ge=0)

    @model_validator(mode="after")
    def validate_groups(self):
        unknown = set(self.required_source_groups) - REQUIRED_NEWS_SOURCE_GROUPS
        if unknown:
            raise ValueError(f"unknown required source groups: {sorted(unknown)}")
        return self


class NewsSourceWatchlistConfig(BaseModel):
    news_source_watchlist: list[NewsSourceWatchlistEntry]
    coverage: NewsSourceCoverageConfig = Field(default_factory=NewsSourceCoverageConfig)

    @model_validator(mode="after")
    def validate_watchlist(self):
        ids = [source.source_id for source in self.news_source_watchlist]
        duplicates = {source_id for source_id in ids if ids.count(source_id) > 1}
        if duplicates:
            raise ValueError(f"duplicate news source ids: {sorted(duplicates)}")
        if not self.news_source_watchlist:
            raise ValueError("at least one news source watchlist entry is required")
        return self


class NewsThemeDefinition(BaseModel):
    theme_id: str
    label: str
    enabled: bool = True


class NewsThemesConfig(BaseModel):
    macro_themes: list[NewsThemeDefinition]
    secular_themes: dict[str, Any] | None = None
    sector_ids: list[str]
    sector_impact_directions: list[str] = Field(
        default_factory=lambda: sorted(ALLOWED_IMPACT_DIRECTIONS)
    )
    theme_directions: list[str] = Field(default_factory=lambda: sorted(ALLOWED_THEME_DIRECTIONS))
    time_horizons: list[str] = Field(default_factory=lambda: sorted(ALLOWED_TIME_HORIZONS))

    @property
    def active_theme_ids(self) -> set[str]:
        return {theme.theme_id for theme in self.macro_themes if theme.enabled}

    @property
    def secular_theme_ids(self) -> set[str]:
        if self.secular_themes is None:
            return set()
        return set(self.secular_themes.keys())

    @model_validator(mode="after")
    def validate_taxonomy(self):
        theme_ids = [theme.theme_id for theme in self.macro_themes]
        duplicates = {theme_id for theme_id in theme_ids if theme_ids.count(theme_id) > 1}
        if duplicates:
            raise ValueError(f"duplicate news theme ids: {sorted(duplicates)}")
        if not self.active_theme_ids:
            raise ValueError("at least one active news theme is required")
        if not self.sector_ids:
            raise ValueError("at least one sector_id is required")
        unknown_impacts = set(self.sector_impact_directions) - ALLOWED_IMPACT_DIRECTIONS
        if unknown_impacts:
            raise ValueError(f"unknown sector impact directions: {sorted(unknown_impacts)}")
        unknown_theme_directions = set(self.theme_directions) - ALLOWED_THEME_DIRECTIONS
        if unknown_theme_directions:
            raise ValueError(f"unknown theme directions: {sorted(unknown_theme_directions)}")
        unknown_horizons = set(self.time_horizons) - ALLOWED_TIME_HORIZONS
        if unknown_horizons:
            raise ValueError(f"unknown time horizons: {sorted(unknown_horizons)}")
        return self


class NewsAIConfig(BaseModel):
    provider: Literal["deepseek", "mock"] = "deepseek"
    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1600, gt=0)
    api_key_env: str = "DEEPSEEK_API_KEY"
    enable_live_ai: bool = False
    mock_mode: bool = True
    request_timeout_seconds: int = Field(default=60, gt=0)
    output_dir: str = "outputs"
    enable_schema_repair: bool = True
    max_retries: int = Field(default=0, ge=0, le=2)
    retry_backoff_seconds: float = Field(default=0.0, ge=0.0)
    max_prompt_body_chars: int = Field(default=8000, gt=0)
    max_retry_response_chars: int = Field(default=4000, gt=0)
    # On a truncated response (finish_reason=length), retry that item with
    # max_tokens scaled by this factor so an oversize article self-heals.
    truncation_retry_multiplier: float = Field(default=2.0, ge=1.0)


class FreshnessDecayConfig(BaseModel):
    enabled: bool = True
    half_life_days: float = Field(default=7.0, gt=0.0)
    max_age_days: int = Field(default=21, ge=0)


class SourceWeightsConfig(BaseModel):
    default: float = Field(default=1.0, ge=0.0)
    sources: dict[str, float] = Field(default_factory=dict)


class NewsScoringConfig(BaseModel):
    aggregation_frequency: list[Literal["daily", "weekly"]] = Field(
        default_factory=lambda: ["daily", "weekly"]
    )
    freshness_decay: FreshnessDecayConfig = Field(default_factory=FreshnessDecayConfig)
    source_weights: SourceWeightsConfig = Field(default_factory=SourceWeightsConfig)
    confidence_weighting: bool = True
    severity_weighting: bool = True
    max_single_item_contribution: float = Field(default=0.35, gt=0.0)
    max_single_source_daily_contribution: float = Field(default=0.75, gt=0.0)
    # Event-level cap: total daily sector contribution from one lexical event
    # (near-duplicate narrative cluster) is clipped here, so one crowded story
    # carried by many articles/sources cannot dominate a sector score.
    max_single_event_daily_contribution: float = Field(default=0.50, gt=0.0)
    event_dedupe_similarity_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    min_confidence: float = Field(default=0.10, ge=0.0, le=1.0)
    min_severity: float = Field(default=0.05, ge=0.0, le=1.0)
    neutral_score_threshold: float = Field(default=0.03, ge=0.0)
    output_start_date: str | None = None
    output_end_date: str | None = None
    output_dir: str = "outputs"

    @model_validator(mode="after")
    def validate_frequencies(self):
        if not self.aggregation_frequency:
            raise ValueError("at least one aggregation frequency is required")
        return self


class SecularNewsScoringConfig(BaseModel):
    aggregation_frequency: list[Literal["monthly", "quarterly"]] = Field(
        default_factory=lambda: ["monthly", "quarterly"]
    )
    freshness_decay: FreshnessDecayConfig = Field(
        default_factory=lambda: FreshnessDecayConfig(half_life_days=30.0, max_age_days=180)
    )
    source_weights: SourceWeightsConfig = Field(default_factory=SourceWeightsConfig)
    confidence_weighting: bool = True
    severity_weighting: bool = True
    max_single_item_contribution: float = Field(default=0.35, gt=0.0)
    max_single_source_period_contribution: float = Field(default=1.50, gt=0.0)
    min_confidence: float = Field(default=0.10, ge=0.0, le=1.0)
    min_severity: float = Field(default=0.05, ge=0.0, le=1.0)
    trend_window_days: int = Field(default=30, gt=0)
    output_dir: str = "outputs"

    @model_validator(mode="after")
    def validate_frequencies(self):
        if not self.aggregation_frequency:
            raise ValueError("at least one secular aggregation frequency is required")
        return self


class SectorNewsIntegrationConfig(BaseModel):
    enabled: bool = True
    macro_sector_weight: float = Field(default=0.75, ge=0.0)
    news_sector_weight: float = Field(default=0.25, ge=0.0)
    news_score_frequency: Literal["daily", "weekly"] = "daily"
    news_score_decay_days: int = Field(default=14, ge=0)
    news_confidence_penalty: float = Field(default=0.05, ge=0.0)
    max_news_adjustment: float = Field(default=0.50, gt=0.0)
    min_news_item_count: int = Field(default=1, ge=0)
    require_recent_news: bool = False
    output_label: str = "experimental_combined_sector_diagnostic"
    output_dir: str = "outputs"

    @model_validator(mode="after")
    def validate_weights(self):
        if self.macro_sector_weight == 0 and self.news_sector_weight == 0:
            raise ValueError("at least one sector diagnostic weight must be positive")
        return self


class NewsMonitoringSourceGroup(BaseModel):
    group_id: str
    target_item_count: int = Field(default=0, ge=0)


class NewsMonitoringFreshnessRules(BaseModel):
    max_age_days: int = Field(default=30, ge=0)
    reject_future_dates: bool = True
    warn_old_items: bool = True
    warn_short_body: bool = True


class NewsMonitoringDuplicateHandling(BaseModel):
    content_hash_dedupe: bool = True
    title_dedupe: bool = True
    source_url_dedupe: bool = True


class NewsMonitoringQualityThresholds(BaseModel):
    min_body_length: int = Field(default=25, ge=0)
    max_failed_classification_rate: float = Field(default=0.10, ge=0.0, le=1.0)
    max_retry_rate: float = Field(default=0.20, ge=0.0, le=1.0)
    max_repair_rate: float = Field(default=0.20, ge=0.0, le=1.0)
    max_truncation_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    min_source_count: int = Field(default=3, ge=0)
    min_date_coverage_days: int = Field(default=3, ge=0)
    max_source_share: float = Field(default=0.35, ge=0.0, le=1.0)
    max_theme_share: float = Field(default=0.45, ge=0.0, le=1.0)
    max_sector_share: float = Field(default=0.45, ge=0.0, le=1.0)
    max_date_share: float = Field(default=0.50, ge=0.0, le=1.0)
    max_old_item_share: float = Field(default=0.20, ge=0.0, le=1.0)
    max_unmapped_pct: float = Field(default=0.20, ge=0.0, le=1.0)
    min_source_groups: int = Field(default=3, ge=0)
    max_single_group_pct: float = Field(default=0.50, ge=0.0, le=1.0)
    max_rank_change: int = Field(default=3, ge=0)
    max_avg_abs_rank_change: float = Field(default=1.5, ge=0.0)


class NewsMonitoringConfig(BaseModel):
    output_dir: str = "outputs"
    news_sources_config: str = "config/news_sources.yaml"
    news_ai_config: str = "config/news_ai.yaml"
    news_themes_config: str = "config/news_themes.yaml"
    news_scoring_config: str = "config/news_scoring.yaml"
    sector_news_integration_config: str = "config/sector_news_integration.yaml"
    source_profile: str = "synthetic_sample"
    source_groups: list[NewsMonitoringSourceGroup]
    freshness_rules: NewsMonitoringFreshnessRules = Field(
        default_factory=NewsMonitoringFreshnessRules
    )
    duplicate_handling: NewsMonitoringDuplicateHandling = Field(
        default_factory=NewsMonitoringDuplicateHandling
    )
    quality_thresholds: NewsMonitoringQualityThresholds = Field(
        default_factory=NewsMonitoringQualityThresholds
    )

    @model_validator(mode="after")
    def validate_source_groups(self):
        group_ids = [group.group_id for group in self.source_groups]
        duplicates = {group_id for group_id in group_ids if group_ids.count(group_id) > 1}
        if duplicates:
            raise ValueError(f"duplicate news monitoring source groups: {sorted(duplicates)}")
        if not group_ids:
            raise ValueError("at least one news monitoring source group is required")
        return self


class NewsSelectionConfig(BaseModel):
    """Pure (no-LLM) prioritization of news items for the classification budget."""

    daily_cap: int = Field(default=120, ge=1)
    min_priority: float = Field(default=0.15, ge=0.0)
    half_life_days: float = Field(default=4.0, gt=0.0)
    max_age_days: int = Field(default=14, ge=0)
    source_authority: dict[str, float] = Field(default_factory=dict)
    group_quota_weights: dict[str, float] = Field(default_factory=dict)
    min_body_words: int = Field(default=25, ge=0)
    drop_likely_non_news: bool = True
    # Cap macro keyword hits so a single keyword-stuffed macro article cannot
    # dominate selection. salience = 1 + min(hits, max_keyword_hits).
    max_keyword_hits: int = Field(default=6, ge=1)
    # Lexical near-duplicate (event) dedupe within the candidate pool. The
    # highest-priority article in a near-duplicate cluster keeps full weight;
    # later duplicates are multiplied by novelty_penalty so one crowded news
    # theme cannot fill the budget with the same story.
    dedupe_near_duplicates: bool = True
    # Token-Jaccard similarity at/above which two articles are the same event.
    novelty_similarity_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    # Multiplier applied to a near-duplicate's priority (0 = drop, 1 = no penalty).
    novelty_penalty: float = Field(default=0.4, ge=0.0, le=1.0)


def load_news_sources_config(path: str | Path = "config/news_sources.yaml") -> NewsSourcesConfig:
    return NewsSourcesConfig.model_validate(_load_yaml(path))


def load_news_selection_config(
    path: str | Path = "config/news_selection.yaml",
) -> NewsSelectionConfig:
    data = _load_yaml(path)
    payload = data.get("news_selection", data)
    return NewsSelectionConfig.model_validate(payload)


def load_news_themes_config(path: str | Path = "config/news_themes.yaml") -> NewsThemesConfig:
    return NewsThemesConfig.model_validate(_load_yaml(path))


def load_news_ai_config(path: str | Path = "config/news_ai.yaml") -> NewsAIConfig:
    data = _load_yaml(path)
    payload = data.get("ai", data)
    return NewsAIConfig.model_validate(payload)


def load_news_scoring_config(
    path: str | Path = "config/news_scoring.yaml",
) -> NewsScoringConfig:
    data = _load_yaml(path)
    payload = data.get("news_scoring", data)
    return NewsScoringConfig.model_validate(payload)


def load_secular_news_scoring_config(
    path: str | Path = "config/news_scoring.yaml",
) -> SecularNewsScoringConfig:
    data = _load_yaml(path)
    payload = data.get("secular_scoring", {})
    return SecularNewsScoringConfig.model_validate(payload)


def load_sector_news_integration_config(
    path: str | Path = "config/sector_news_integration.yaml",
) -> SectorNewsIntegrationConfig:
    data = _load_yaml(path)
    payload = data.get("sector_news_integration", data)
    return SectorNewsIntegrationConfig.model_validate(payload)


def load_news_monitoring_config(
    path: str | Path = "config/news_monitoring.yaml",
) -> NewsMonitoringConfig:
    data = _load_yaml(path)
    payload = data.get("news_monitoring", data)
    return NewsMonitoringConfig.model_validate(payload)


def load_news_source_watchlist_config(
    path: str | Path = "config/news_source_watchlist.yaml",
) -> NewsSourceWatchlistConfig:
    data = _load_yaml(path)
    return NewsSourceWatchlistConfig.model_validate(data)


def _load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}
