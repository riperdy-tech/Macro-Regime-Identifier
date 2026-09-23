"""Capital-market anchors: regime-conditional multiple bands.

The important properties are: conditioning actually conditions (no look-ahead into a
future macro state), the fallback ladder is honest about which rung produced a number,
the Gordon leg refuses to divide by a collapsed spread, and with no valuation panel the
market-observed leg is `unavailable` rather than invented.
"""

from __future__ import annotations

import pandas as pd
import pytest

from macro_engine.anchors.config import load_anchor_config
from macro_engine.anchors.multiples import (
    all_state_keys,
    build_multiple_bands_payload,
    build_regime_state_frame,
    conditional_bands,
    current_state,
    gordon_justified_multiple,
    load_valuation_panel,
)

AS_OF = pd.Timestamp("2026-04-30")


def _state_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2024-01-01", "2024-07-01", "2025-01-01", "2025-07-01", "2026-01-01"]
            ),
            "growth": ["weak", "strong", "strong", "weak", "strong"],
            "inflation": ["low", "high", "high", "low", "high"],
            "credit": ["tight", "loose", "loose", "tight", "loose"],
            "rate_band": ["low", "high", "high", "low", "high"],
        }
    )


def _panel(sector_id: str = "energy") -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=40, freq="MS")
    return pd.DataFrame(
        {
            "date": dates,
            "sector_id": sector_id,
            "multiple": [10.0 + index * 0.5 for index in range(40)],
        }
    )


def test_regime_state_is_derived_from_stored_dimension_history():
    dimension_scores = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]),
            "dimension_id": ["growth_momentum"] * 3,
            "score": [1.2, -0.9, 0.1],
        }
    )
    observations = pd.DataFrame(
        {
            "series_id": ["DGS10"] * 3,
            "date": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]),
            "value": [2.0, 4.5, 3.5],
        }
    )
    state = build_regime_state_frame(
        dimension_scores=dimension_scores,
        observations=observations,
        config=load_anchor_config("config/anchors.yaml"),
    )
    assert list(state["growth"]) == ["strong", "weak", "neutral"]
    assert list(state["rate_band"]) == ["low", "high", "mid"]
    # Dimensions with no history report `unknown`, never a guessed bucket.
    assert set(state["inflation"]) == {"unknown"}


def test_state_labels_are_looked_up_as_of_and_never_forward():
    state = _state_frame()
    assert current_state(state, pd.Timestamp("2025-03-01"))["growth"] == "strong"
    assert current_state(state, pd.Timestamp("2026-04-30"))["growth"] == "strong"
    # Before the first state row there is no state at all.
    assert current_state(state, pd.Timestamp("2023-01-01")) == {}


def _monthly_state_frame(pattern: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    """One state row per month, starting 2024-01-01, cycling `pattern`."""
    dates = pd.date_range("2024-01-01", periods=40, freq="MS")
    rows = [pattern[index % len(pattern)] for index in range(40)]
    return pd.DataFrame(
        {
            "date": dates,
            "growth": [row[0] for row in rows],
            "inflation": [row[1] for row in rows],
            "credit": [row[2] for row in rows],
            "rate_band": [row[3] for row in rows],
        }
    )


def test_conditioning_excludes_observations_from_other_macro_states():
    """Only months in the CURRENT macro state may enter the published band."""
    # Half the months are strong, half weak; the current state is strong.
    state = _monthly_state_frame([("strong", "high", "loose", "high"), ("weak", "low", "tight", "low")])
    panel = _panel()
    config = load_anchor_config("config/anchors.yaml")
    config.multiple_bands.min_observations = 3
    config.multiple_bands.conditioning_ladder = [["growth"]]
    bands, _ = conditional_bands(
        panel=panel,
        state_frame=state,
        sectors=["energy"],
        config=config,
        as_of=AS_OF,
    )
    band = bands["energy"]
    matching = panel.merge(state, on="date", how="inner")
    expected = len(matching[matching["growth"] == "strong"])
    assert band.conditioning_level == ["growth"]
    assert expected == 20
    assert band.n_obs == expected < len(panel)


def test_conditioning_ladder_falls_back_and_reports_which_rung_was_used():
    """When the fully-conditioned cell is too thin, the band says which rung it used."""
    # The CURRENT state (month index 27 = 2026-04-01) occurs in only 4 months, while
    # growth=strong alone spans 28. A floor of 10 is satisfiable only by the coarser rung.
    pattern = (
        [("strong", "low", "tight", "high")] * 24
        + [("strong", "high", "loose", "high")] * 4
        + [("weak", "low", "tight", "low")] * 12
    )
    state = _monthly_state_frame(pattern)
    config = load_anchor_config("config/anchors.yaml")
    config.multiple_bands.min_observations = 10
    bands, _ = conditional_bands(
        panel=_panel(),
        state_frame=state,
        sectors=["energy"],
        config=config,
        as_of=AS_OF,
    )
    band = bands["energy"]
    assert band.regime_state == {
        "growth": "strong",
        "inflation": "high",
        "credit": "loose",
        "rate_band": "high",
    }
    assert band.conditioning_level == ["growth"]
    assert band.n_obs == 28


def test_a_cell_too_thin_for_every_rung_publishes_no_quantiles():
    """Even the unconditional rung is a sample and must clear the floor."""
    config = load_anchor_config("config/anchors.yaml")
    config.multiple_bands.min_observations = 10_000
    bands, reasons = conditional_bands(
        panel=_panel(),
        state_frame=_state_frame(),
        sectors=["energy"],
        config=config,
        as_of=AS_OF,
    )
    band = bands["energy"]
    assert band.n_obs == 0
    assert band.ntm_pe == {}
    assert any("no conditioning cell" in reason for reason in reasons)


def test_quantiles_are_ordered_and_match_the_underlying_sample():
    config = load_anchor_config("config/anchors.yaml")
    config.multiple_bands.min_observations = 3
    panel = _panel()
    bands, _ = conditional_bands(
        panel=panel, state_frame=_state_frame(), sectors=["energy"], config=config, as_of=AS_OF
    )
    band = bands["energy"]
    assert band.ntm_pe["p25"] <= band.ntm_pe["median"] <= band.ntm_pe["p75"]
    assert band.ntm_pe["median"] == pytest.approx(panel["multiple"].median(), abs=5.0)


def test_all_state_keys_covers_every_rung_of_the_ladder():
    """Attaching only the finest rung's keys would make the coarser rungs unreachable."""
    ladder = [["growth", "inflation", "rate_band", "credit"], ["growth", "inflation"], ["growth"], []]
    assert all_state_keys(ladder) == ["credit", "growth", "inflation", "rate_band"]


def test_gordon_cross_check_is_consistent_with_its_inputs():
    check, reasons = gordon_justified_multiple(
        cost_of_equity=0.09,
        growth=0.025,
        payout_ratio=0.60,
        min_spread=0.005,
        sanity_band=(5.0, 60.0),
    )
    assert check["justified_pe"] == pytest.approx(0.60 / (0.09 - 0.025), abs=1e-3)
    assert reasons == []


def test_gordon_refuses_to_publish_a_multiple_off_a_collapsed_spread():
    """Gordon breaking down must be reported, not emitted as a huge number."""
    check, reasons = gordon_justified_multiple(
        cost_of_equity=0.027,
        growth=0.025,
        payout_ratio=0.60,
        min_spread=0.005,
        sanity_band=(5.0, 60.0),
    )
    assert check["justified_pe"] is None
    assert any("broken down" in reason for reason in reasons)


def test_gordon_flags_a_multiple_outside_the_sanity_band():
    check, reasons = gordon_justified_multiple(
        cost_of_equity=0.031,
        growth=0.025,
        payout_ratio=0.99,
        min_spread=0.005,
        sanity_band=(5.0, 60.0),
    )
    assert check["justified_pe"] == pytest.approx(0.99 / 0.006, abs=0.01)
    assert any("sanity band" in reason for reason in reasons)


def test_gordon_withholds_the_multiple_without_inputs():
    check, reasons = gordon_justified_multiple(
        cost_of_equity=None,
        growth=0.025,
        payout_ratio=0.60,
        min_spread=0.005,
        sanity_band=(5.0, 60.0),
    )
    assert check["justified_pe"] is None
    assert any("unavailable" in reason for reason in reasons)


def test_panel_loader_reports_absence_instead_of_inventing_bands():
    """With no panel configured, the loader returns nothing — never a default band.

    Asserted explicitly rather than relying on the shipped default, which now points at a real
    panel: a test that read the default would stop testing this the moment one was built.
    """
    config = load_anchor_config("config/anchors.yaml")
    config.multiple_bands.panel.source = "none"
    config.multiple_bands.panel.path = None
    panel, source, reasons = load_valuation_panel(config=config, sector_map={})
    assert panel.empty
    assert source is None
    assert reasons == []


def test_panel_loader_derives_the_multiple_from_eps_when_needed(tmp_path):
    path = tmp_path / "panel.csv"
    path.write_text(
        "date,sector_id,eps_ntm,close\n"
        "2026-01-31,energy,8.0,80.0\n"
        "2026-02-28,energy,8.0,88.0\n"
        "2026-03-31,energy,-1.0,50.0\n",
        encoding="utf-8",
    )
    config = load_anchor_config("config/anchors.yaml")
    config.multiple_bands.panel.source = "external_valuation_panel"
    config.multiple_bands.panel.path = str(path)
    # Point the loader at THIS fixture's columns rather than the shipped panel's.
    config.multiple_bands.panel.multiple_column = "pe_absent"
    config.multiple_bands.panel.eps_column = "eps_ntm"
    config.multiple_bands.panel.price_column = "close"
    panel, source, reasons = load_valuation_panel(config=config, sector_map={})
    assert source == "external_valuation_panel"
    assert reasons == []
    # 80/8 = 10x and 88/8 = 11x; the negative-EPS row is refused, not turned into -50x.
    assert sorted(panel["multiple"].round(3)) == [10.0, 11.0]


def test_panel_loader_rejects_a_price_only_panel(tmp_path):
    """The local sector ETF file is price-only: it cannot yield a multiple."""
    path = tmp_path / "prices.csv"
    path.write_text("ticker,date,close\nSPY,2026-01-31,500.0\n", encoding="utf-8")
    config = load_anchor_config("config/anchors.yaml")
    config.multiple_bands.panel.source = "external_valuation_panel"
    config.multiple_bands.panel.path = str(path)
    panel, source, reasons = load_valuation_panel(config=config, sector_map={})
    assert panel.empty and source is None
    assert any("needs" in reason for reason in reasons)


def test_band_measure_is_labelled_because_ntm_pe_may_be_trailing(tmp_path):
    """The quantiles live under `ntm_pe`, but the MEASURE is whatever was actually built.

    The shipped panel is a trailing multiple. Publishing it under a forward-sounding key
    without a label is the mislabelling the anchors exist to remove, so every band carries
    `measure` and `measure_definition`.
    """
    config = load_anchor_config("config/anchors.yaml")
    config.multiple_bands.panel.multiple_measure = "pe_ttm"
    config.multiple_bands.panel.multiple_definition = "price / last reported FY EPS"
    config.multiple_bands.min_observations = 3
    payload = build_multiple_bands_payload(
        panel=_panel(),
        state_frame=_monthly_state_frame([("strong", "high", "loose", "high")] * 40),
        sectors=["energy"],
        config=config,
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        cost_of_equity=0.09,
        growth=0.025,
        panel_source="external_valuation_panel",
    )
    band = payload.bands[0]
    assert band.ntm_pe["median"]  # quantiles under the contract key
    assert band.measure == "pe_ttm"  # ... labelled with what they actually are
    assert band.measure_definition
    assert payload.panel_measure == "pe_ttm"


def test_panel_caveats_are_carried_onto_the_payload(tmp_path):
    """A panel's limitations must travel with its numbers, not live only in a script comment."""
    import json as _json

    meta = tmp_path / "panel.meta.json"
    meta.write_text(
        _json.dumps({"caveats": ["SURVIVORSHIP: the universe is today's listings."]}),
        encoding="utf-8",
    )
    config = load_anchor_config("config/anchors.yaml")
    config.multiple_bands.panel.meta_path = str(meta)
    config.multiple_bands.min_observations = 3
    payload = build_multiple_bands_payload(
        panel=_panel(),
        state_frame=_monthly_state_frame([("strong", "high", "loose", "high")] * 40),
        sectors=["energy"],
        config=config,
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        cost_of_equity=0.09,
        growth=0.025,
        panel_source="external_valuation_panel",
    )
    assert payload.panel_meta
    assert any(
        "SURVIVORSHIP" in reason for reason in payload.provenance.degradation_reasons
    )


def test_sub_industries_are_reported_as_absent_not_silently_zeroed():
    """The sector-level panel cannot cover sub-industries; that must be visible."""
    config = load_anchor_config("config/anchors.yaml")
    config.multiple_bands.min_observations = 3
    bands, reasons = conditional_bands(
        panel=_panel(sector_id="energy"),
        state_frame=_monthly_state_frame([("strong", "high", "loose", "high")] * 40),
        sectors=["energy", "semiconductors"],
        config=config,
        as_of=AS_OF,
    )
    assert bands["semiconductors"].n_obs == 0
    assert bands["semiconductors"].ntm_pe == {}
    assert any("semiconductors" in reason for reason in reasons)


def test_payload_marks_the_market_observed_leg_unavailable_without_a_panel():
    payload = build_multiple_bands_payload(
        panel=pd.DataFrame(),
        state_frame=_state_frame(),
        sectors=["energy", "utilities"],
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        cost_of_equity=0.09,
        growth=0.025,
        panel_source=None,
    )
    assert payload.panel_source == "unavailable"
    assert payload.degraded is True
    assert {band.sector_id for band in payload.bands} == {"energy", "utilities"}
    assert all(band.ntm_pe == {} for band in payload.bands)
    assert all(band.n_obs == 0 for band in payload.bands)
    # The arithmetic leg survives, because its inputs are FRED-provable.
    assert all(
        band.arithmetic_check["justified_pe"] == pytest.approx(0.60 / (0.09 - 0.025), abs=1e-3)
        for band in payload.bands
    )
    assert any(
        "requires a valuation panel" in reason
        for reason in payload.provenance.degradation_reasons
    )


# ── P0.3: the regime leg's own provenance and staleness (folded into S2) ───────────────────
# The season LABEL left the growth anchor in S2 (P0_0 §5.2), but this payload's
# `regime_state` buckets still condition on the dimension-derived macro state, so THAT
# read must still disclose its own date/age and degrade when stale.


def test_regime_leg_provenance_is_disclosed_and_not_stale_when_fresh():
    fresh_state = _state_frame().copy()
    fresh_state.loc[fresh_state.index[-1], "date"] = AS_OF - pd.Timedelta(days=10)
    payload = build_multiple_bands_payload(
        panel=_panel(),
        state_frame=fresh_state,
        sectors=["energy"],
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        cost_of_equity=0.09,
        growth=0.025,
        panel_source="external_valuation_panel",
    )
    leg = payload.provenance.regime_leg
    assert leg["used"] is True
    assert leg["date"] == (AS_OF - pd.Timedelta(days=10)).date().isoformat()
    assert leg["age_days"] == 10
    assert leg["regime_or_state"] == current_state(fresh_state, AS_OF)
    assert not any(r.startswith("regime_leg_stale:") for r in payload.provenance.degradation_reasons)


def test_regime_leg_degrades_the_payload_once_it_crosses_the_max_age():
    from macro_engine.regime_status import CURRENT_REGIME_MAX_AGE_DAYS

    stale_state = _state_frame().copy()
    stale_date = AS_OF - pd.Timedelta(days=CURRENT_REGIME_MAX_AGE_DAYS + 1)
    stale_state.loc[stale_state.index[-1], "date"] = stale_date
    payload = build_multiple_bands_payload(
        panel=_panel(),
        state_frame=stale_state,
        sectors=["energy"],
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        cost_of_equity=0.09,
        growth=0.025,
        panel_source="external_valuation_panel",
    )
    leg = payload.provenance.regime_leg
    assert leg["used"] is True
    assert leg["age_days"] == CURRENT_REGIME_MAX_AGE_DAYS + 1
    assert payload.degraded is True
    assert f"regime_leg_stale:{stale_date.date().isoformat()}" in payload.provenance.degradation_reasons


def test_regime_leg_is_null_dated_when_no_macro_state_is_stored():
    payload = build_multiple_bands_payload(
        panel=pd.DataFrame(),
        state_frame=pd.DataFrame(columns=["date", "growth", "inflation", "credit", "rate_band"]),
        sectors=["energy"],
        config=load_anchor_config("config/anchors.yaml"),
        as_of=AS_OF,
        built_at="2026-04-30T00:00:00+00:00",
        cost_of_equity=0.09,
        growth=0.025,
        panel_source=None,
    )
    leg = payload.provenance.regime_leg
    assert leg["used"] is True
    assert leg["date"] is None
    assert leg["age_days"] is None
