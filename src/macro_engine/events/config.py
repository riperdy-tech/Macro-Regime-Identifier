"""Loader for the frozen events parameters (`config/events.yaml`).

Every number the E1 builder uses is read here. Nothing in `builder.py` carries a literal threshold.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from macro_engine import peer_paths

LINKING_MODES = ("v2", "v2_2")
DEFAULT_EVENTS_PATH = peer_paths.REPO_ROOT / "config" / "events.yaml"


@dataclass(frozen=True)
class E1Config:
    version: int
    pool_window_days: float
    syndication_jaccard_min: float
    link_jaccard_min: float
    unclassified_link_jaccard_min: float
    entity_relevance_min: float
    horizon_hours: float
    new_development_jaccard_below: float
    min_distinct_publishers: int
    official_source_corroborates: bool
    rejection_hours: float
    official_source_domains: frozenset[str]
    official_domains_status: str | None
    generic_entities_ref: str
    linking: str = "v2_2"
    aggregator_hosts: frozenset[str] = frozenset()
    publisher_suffix_pattern: str = ""
    background_days: float = 28.0
    background_max_share: float = 0.01
    background_min_items: int = 300


def _value(node: Any, path: str) -> Any:
    cur = node
    for part in path.split("."):
        cur = cur[part]
    if not isinstance(cur, dict) or "value" not in cur:
        raise KeyError(f"events.yaml: {path} has no value")
    return cur["value"]


def host_key(domain: str | None) -> str | None:
    """Lower-cased host with a leading `www.` removed (publisher / official-domain comparison key)."""
    if domain is None:
        return None
    host = str(domain).strip().casefold()
    if host.startswith("www."):
        host = host[4:]
    return host or None


def load_e1_config(path: str | Path = DEFAULT_EVENTS_PATH) -> E1Config:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    e1 = doc["e1"]
    un = doc["unratified"]
    linking = str(un["linking"]["value"])
    if linking not in LINKING_MODES:
        raise ValueError(f"events.yaml: unratified.linking must be one of {LINKING_MODES}, got {linking!r}")
    official = e1["corroboration"]["official_source_domains"]
    return E1Config(
        version=int(doc["version"]),
        pool_window_days=float(_value(e1, "pool_window_days")),
        syndication_jaccard_min=float(_value(e1, "syndication.title_jaccard_min")),
        link_jaccard_min=float(_value(e1, "link.title_jaccard_min")),
        unclassified_link_jaccard_min=float(_value(e1, "link.unclassified_title_jaccard_min")),
        entity_relevance_min=float(_value(e1, "link.entity_relevance_min")),
        horizon_hours=float(_value(e1, "link.horizon_hours")),
        new_development_jaccard_below=float(_value(e1, "new_development.title_jaccard_below")),
        min_distinct_publishers=int(_value(e1, "corroboration.min_distinct_publishers")),
        official_source_corroborates=bool(_value(e1, "corroboration.official_source_corroborates")),
        rejection_hours=float(_value(e1, "corroboration.rejection_hours")),
        official_source_domains=frozenset(
            k for k in (host_key(d) for d in official["value"]) if k is not None
        ),
        official_domains_status=official.get("status"),
        generic_entities_ref=str(_value(e1, "link.generic_entity_stop_list")),
        linking=linking,
        aggregator_hosts=frozenset(k for k in (host_key(h) for h in un["aggregator_hosts"]["value"]) if k is not None),
        publisher_suffix_pattern=str(un["publisher_suffix_pattern"]["value"]),
        background_days=float(un["entity_background"]["days_before_pool"]),
        background_max_share=float(un["entity_background"]["max_share"]),
        background_min_items=int(un["entity_background"]["min_items"]),
    )


def load_generic_entities(ref: str) -> list[str]:
    """Resolve `path#key` (path relative to the repository root) to the stop list it names."""
    file_part, _, key = ref.partition("#")
    if not key:
        raise ValueError(f"generic entity reference has no '#key': {ref!r}")
    doc = yaml.safe_load((peer_paths.REPO_ROOT / file_part).read_text(encoding="utf-8")) or {}
    return [str(x) for x in doc[key]]
