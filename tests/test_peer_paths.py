"""Unit tests for the peer-repository path contract (src/macro_engine/peer_paths.py).

Three absolute Windows paths used to be typed into this repo - the screener's public/data twice,
and RS2's config.json once. This module holds them once, and must resolve under both the current
nested layout and the planned sibling layout with no edit in between.

The asymmetry worth pinning: the screener is a sibling of this repo in BOTH layouts, but RS2 is
not. Today RS2 lives one level further up, outside the `Stock Screener` wrapper; after the move it
becomes a sibling. So the parent and the grandparent are both searched.
"""

from __future__ import annotations

import pytest

from macro_engine import peer_paths

PEER_VARS = ("STOCKS_ROOT", "SCREENER_DATA_DIR", "RS2_CONFIG_PATH")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No test may depend on this machine's real layout or on a stray variable."""
    for var in PEER_VARS:
        monkeypatch.delenv(var, raising=False)


def test_current_nested_layout_resolves(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_ROOT", str(tmp_path))
    data = tmp_path / "Stock Screener" / "public" / "data"
    data.mkdir(parents=True)
    rs2 = tmp_path / "RS2 Local"
    rs2.mkdir()
    (rs2 / "config.json").write_text("{}", encoding="utf-8")

    assert peer_paths.screener_data_dir() == data
    assert peer_paths.rs2_config_path() == rs2 / "config.json"


def test_post_move_sibling_layout_resolves(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_ROOT", str(tmp_path))
    data = tmp_path / "stock-screener" / "public" / "data"
    data.mkdir(parents=True)
    rs2 = tmp_path / "rs2-local"
    rs2.mkdir()
    (rs2 / "config.json").write_text("{}", encoding="utf-8")

    assert peer_paths.screener_data_dir() == data
    assert peer_paths.rs2_config_path() == rs2 / "config.json"


def test_current_spelling_wins_while_both_exist(tmp_path, monkeypatch):
    """During the move both can be on disk. Resolving to a freshly created empty sibling would
    silently build anchors from an empty corpus, which is worse than failing."""
    monkeypatch.setenv("STOCKS_ROOT", str(tmp_path))
    nested = tmp_path / "Stock Screener" / "public" / "data"
    nested.mkdir(parents=True)
    (tmp_path / "stock-screener" / "public" / "data").mkdir(parents=True)

    assert peer_paths.screener_data_dir() == nested


def test_rs2_is_found_one_level_above_the_screener_today(tmp_path, monkeypatch):
    """The real current layout: this repo inside a wrapper, RS2 outside it."""
    repo = tmp_path / "Stock Screener" / "Macro Regime Indicator"
    (repo / "src" / "macro_engine").mkdir(parents=True)
    data = tmp_path / "Stock Screener" / "Stock Screener" / "public" / "data"
    data.mkdir(parents=True)
    rs2 = tmp_path / "RS2 Local"
    rs2.mkdir()
    (rs2 / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(peer_paths, "REPO_ROOT", repo)

    assert peer_paths.screener_data_dir() == data
    assert peer_paths.rs2_config_path() == rs2 / "config.json"


def test_env_override_is_used_verbatim_even_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_ROOT", str(tmp_path))
    (tmp_path / "stock-screener" / "public" / "data").mkdir(parents=True)
    monkeypatch.setenv("SCREENER_DATA_DIR", str(tmp_path / "typo"))

    assert peer_paths.screener_data_dir() == tmp_path / "typo"


def test_rs2_config_override_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("RS2_CONFIG_PATH", str(tmp_path / "elsewhere" / "config.json"))

    assert peer_paths.rs2_config_path() == tmp_path / "elsewhere" / "config.json"


def test_absent_peers_are_none_not_a_wrong_guess(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_ROOT", str(tmp_path))

    assert peer_paths.screener_data_dir() is None
    assert peer_paths.rs2_config_path() is None


def test_required_raises_and_names_every_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_ROOT", str(tmp_path))

    with pytest.raises(FileNotFoundError) as excinfo:
        peer_paths.screener_data_dir(required=True)
    msg = str(excinfo.value)
    assert "SCREENER_DATA_DIR" in msg
    assert "Stock Screener" in msg and "stock-screener" in msg
    assert "do not guess" in msg

    with pytest.raises(FileNotFoundError) as excinfo:
        peer_paths.rs2_config_path(required=True)
    assert "RS2_CONFIG_PATH" in str(excinfo.value)


def test_anchor_age_limit_falls_back_when_rs2_is_absent(tmp_path, monkeypatch):
    """`daily_health` must still run with no RS2 checkout present - MRI is not allowed to require
    its consumer to exist. 45 is the documented fallback."""
    from macro_engine import daily_health

    monkeypatch.setenv("STOCKS_ROOT", str(tmp_path))

    assert daily_health._anchor_age_limit() == 45


def test_anchor_age_limit_reads_rs2s_number_when_reachable(tmp_path, monkeypatch):
    """MRI must not invent its own staleness limit: two limits would eventually disagree."""
    from macro_engine import daily_health

    rs2 = tmp_path / "rs2-local"
    rs2.mkdir()
    (rs2 / "config.json").write_text('{"anchor_max_age_days": 30}', encoding="utf-8")
    monkeypatch.setenv("STOCKS_ROOT", str(tmp_path))

    assert daily_health._anchor_age_limit() == 30
