"""Dense title embeddings (events arm B): the store, the pin check, and arm B linking with synthetic vectors.
Nothing here needs the model or its packages except the tests marked by `needs_model`, which skip cleanly."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

import duckdb
import numpy as np
import pytest
from test_events_builder import CFG, mk

from macro_engine.events.embeddings import (
    DEFAULT_MODEL_DIR,
    PREPROCESS_VERSION,
    EmbeddingError,
    EmbeddingModel,
    EmbeddingStore,
    ModelPin,
    dense_cosine,
    embed_items,
    file_sha256,
    item_text,
    load_model_pin,
)
from macro_engine.events.linker import (
    ArmAConfig,
    ArmALinker,
    ArmBConfig,
    ArmBLinker,
    Background,
    configured_arm,
    load_arm_b_config,
    make_background,
)
from macro_engine.events.state import EventState, EventSummary, Report, ReportRef

D0 = datetime(2026, 10, 10, 6, 0)


def _have_runtime() -> bool:
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError:
        return False
    return True


def _pin_ok() -> bool:
    try:
        pin = load_model_pin()
        d = DEFAULT_MODEL_DIR / pin.name
        return (d / "model.onnx").is_file() and file_sha256(d / "model.onnx") == pin.model_sha256
    except Exception:
        return False


needs_model = pytest.mark.skipif(not (_have_runtime() and _pin_ok()),
                                 reason="optional extra `embeddings` or the pinned model files are not installed")


def unit(*xs: float) -> np.ndarray:
    v = np.array(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


# ------------------------------------------------------------------ store


def test_store_roundtrip_is_float32_and_never_overwrites():
    con = duckdb.connect(":memory:")
    st = EmbeddingStore(con)
    vecs = np.stack([unit(1, 2, 3), unit(3, 2, 1)])
    assert st.put(["a", "b"], vecs, "m", "sha", embedded_at=datetime(2026, 10, 18)) == 2
    assert st.put(["a"], np.stack([unit(9, 9, 9)]), "m", "sha") == 0          # idempotent: existing row is kept
    got = st.load_vectors(["a", "b", "zzz"], "m", "sha")
    assert set(got) == {"a", "b"} and got["a"].dtype == np.float32
    assert np.array_equal(got["a"], vecs[0])
    row = con.execute(
        "select model_name, model_sha256, preprocess_version, dim from event_embeddings where news_id='a'").fetchone()
    assert row == ("m", "sha", PREPROCESS_VERSION, 3)
    assert st.load_vectors(["a"], "m", "other-sha") == {}                   # provenance is part of the key
    assert st.existing_ids("m", "sha") == {"a", "b"}


class _FakeModel:
    name, sha256 = "fake", "f" * 64

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return np.stack([unit(len(t) + 1.0, 1.0) for t in texts])


def test_embed_items_is_idempotent_and_embeds_the_canonical_title():
    con = duckdb.connect(":memory:")
    st = EmbeddingStore(con)
    items = [mk("n1", "  Oil  UP ", 0), mk("n2", "Gold down", 1)]
    fake = _FakeModel()
    out = embed_items(st, fake, items, batch_size=1, config=CFG)  # type: ignore[arg-type]
    assert out == {"requested": 2, "already_stored": 0, "embedded": 2, "stored": 2}
    assert fake.calls == [["oil up"], ["gold down"]] and item_text(items[0], CFG) == "oil up"
    again = embed_items(st, fake, items, config=CFG)  # type: ignore[arg-type]
    assert again["embedded"] == 0 and len(fake.calls) == 2                  # nothing recomputed


def test_dense_cosine_handles_missing():
    assert dense_cosine(None, unit(1, 0)) is None and dense_cosine(unit(1, 0), None) is None
    assert dense_cosine(unit(1, 0), unit(1, 0)) == pytest.approx(1.0)


# ------------------------------------------------------------------ pin


def test_model_refuses_to_run_on_a_hash_mismatch(tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "model.onnx").write_bytes(b"not the pinned model")
    (d / "tokenizer.json").write_bytes(b"{}")
    pin = ModelPin("m", "0" * 64, "1" * 64)
    with pytest.raises(EmbeddingError, match="sha256 mismatch"):
        EmbeddingModel.load(pin, d)
    with pytest.raises(EmbeddingError, match="not found"):
        EmbeddingModel.load(pin, tmp_path / "absent")


def test_config_pin_matches_code_preprocessing():
    pin = load_model_pin()
    assert pin.name and len(pin.model_sha256) == 64 and len(pin.tokenizer_sha256) == 64


@needs_model
def test_pinned_model_embeds_unit_vectors_deterministically():
    model = EmbeddingModel.load(load_model_pin())
    texts = ["houthi missile attack hits riyadh airport", "houthi drone strike on saudi airport", "fed holds rates steady"]
    a, b = model.embed(texts), model.embed(texts[::-1])[::-1]
    assert a.dtype == np.float32 and a.shape == (3, 384)
    assert np.allclose(np.linalg.norm(a, axis=1), 1.0, atol=1e-5)
    assert np.max(np.abs(a - b)) < 1e-5                                      # batch order drift is tiny (measured 0.0)
    assert a[0] @ a[1] > a[0] @ a[2]


# ------------------------------------------------------------------ arm B


def rep(rid, title, hours=0, ents=()):
    seen = D0 + timedelta(hours=hours)
    return Report(rid, title, seen, seen - timedelta(hours=1), "pub", frozenset(ents))


def empty_bg() -> Background:
    return make_background([], ArmAConfig())


def event_of(anchor_titles):
    refs = tuple(ReportRef(f"a{k}", t, D0, D0, frozenset()) for k, t in enumerate(anchor_titles))
    return EventSummary("evt_x", refs, (), frozenset(), len(refs), D0, D0)


def lkb(vectors, **arm) -> ArmBLinker:
    return ArmBLinker(ArmBConfig(**arm), empty_bg(), vectors)


def test_arm_b_adds_the_dense_term_to_the_arm_a_score_exactly():
    ev = event_of(["alpha beta gamma"])
    r = rep("r", "alpha beta delta", 5)
    vecs = {"a0": unit(1, 0), "r": unit(1, 1)}
    a = ArmALinker(ArmAConfig(cutoff=0.1), empty_bg()).link(r, [ev])
    b = lkb(vecs, cutoff=0.1, dense_weight=2.0).link(r, [ev])
    cos = float(unit(1, 0) @ unit(1, 1))
    assert b.evidence["arm"] == "B" and a.evidence["arm"] == "A" and "dense_cosine" not in a.evidence
    assert b.evidence["dense_cosine"] == pytest.approx(cos, abs=1e-6)
    assert b.evidence["score"] == pytest.approx(a.evidence["score"] + 2.0 * cos, abs=1e-5)


def test_dense_term_can_link_what_the_title_alone_cannot():
    ev = event_of(["houthi strike riyadh airport"])
    r = rep("r", "saudi capital hit by yemen drone", 5)                       # almost no shared characters
    vecs = {"a0": unit(1, 0.1), "r": unit(1, 0.2)}
    assert ArmALinker(ArmAConfig(cutoff=0.6), empty_bg()).link(r, [ev]).event_id is None
    assert lkb(vecs, cutoff=0.6, dense_weight=1.0, title_cosine_floor=0.0).link(r, [ev]).event_id == "evt_x"


def test_missing_vector_falls_back_to_arm_a_and_is_annotated_never_dropped():
    ev = event_of(["alpha beta gamma"])
    r = rep("r", "alpha beta delta", 5)
    a = ArmALinker(ArmAConfig(cutoff=0.1), empty_bg()).link(r, [ev])
    for vecs in ({}, {"a0": unit(1, 0)}, {"r": unit(1, 0)}):                 # report, anchor, or both missing
        b = lkb(vecs, cutoff=0.1, dense_weight=5.0).link(r, [ev])
        assert "dense_missing" in b.evidence["flags"]
        assert b.event_id == a.event_id and b.evidence["score"] == a.evidence["score"]
        assert b.evidence["dense_cosine"] is None


def test_complete_vectors_carry_no_dense_missing_flag():
    ev = event_of(["alpha beta gamma"])
    b = lkb({"a0": unit(1, 0), "r": unit(1, 0)}, cutoff=0.1).link(rep("r", "alpha beta delta", 5), [ev])
    assert "dense_missing" not in b.evidence["flags"]


def test_best_anchor_is_chosen_on_the_summed_score():
    ev = event_of(["zzz qqq www", "alpha beta gamma"])
    r = rep("r", "alpha beta gamma", 5)
    vecs = {"a0": unit(1, 0), "a1": unit(0, 1), "r": unit(1, 0)}             # dense favours anchor a0, title favours a1
    b = lkb(vecs, cutoff=0.1, dense_weight=0.1, title_cosine_floor=0.0).link(r, [ev])
    assert b.evidence["anchor"] == "a1"
    b = lkb(vecs, cutoff=0.1, dense_weight=5.0, title_cosine_floor=0.0).link(r, [ev])
    assert b.evidence["anchor"] == "a0"


def test_arm_b_runs_through_the_state():
    state = EventState(duckdb.connect(":memory:"))
    linker = lkb({"a1": unit(1, 0), "a2": unit(1, 0.05)}, cutoff=0.3, dense_weight=1.0)
    state.apply_day(datetime(2026, 10, 10, 23, 59),
                    [rep("a1", "tanker seized in gulf", 0), rep("a2", "tanker seized gulf", 1),
                     rep("a3", "tanker seized in the gulf", 2)], linker)       # a3 has no stored vector
    assert state.event_of_report("a1") == state.event_of_report("a2") == state.event_of_report("a3")


def test_config_arm_b_overlays_arm_a_and_arm_defaults_to_a(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("arm_a:\n  cutoff: 0.3\n  background_days: 21\narm_b:\n  cutoff: 0.8\n  dense_weight: 1.5\n", encoding="utf-8")
    cfg = load_arm_b_config(p)
    assert (cfg.cutoff, cfg.dense_weight, cfg.background_days) == (0.8, 1.5, 21.0)
    assert configured_arm(p) == "A"
    p.write_text("arm: B\narm_a:\n  cutoff: 0.3\n", encoding="utf-8")
    assert configured_arm(p) == "B"
    p.write_text("arm: C\narm_a:\n  cutoff: 0.3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="arm must be"):
        configured_arm(p)
    p.write_text("arm_a:\n  cutoff: 0.3\narm_b:\n  surprise: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="surprise"):
        load_arm_b_config(p)


def test_shipped_config_loads_for_both_arms():
    assert configured_arm() in ("A", "B")
    assert isinstance(dataclasses.asdict(load_arm_b_config()), dict)
