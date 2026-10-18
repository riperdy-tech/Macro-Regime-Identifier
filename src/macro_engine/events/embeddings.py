"""Dense title embeddings for arm B (docs/events_build/V23_PREREGISTRATION.md, section 1; BRIEF_P4C.md).

  * ONE pinned model, an ONNX graph run on CPU through `onnxruntime`, with its `tokenizers` tokenizer. No torch, no
    sentence-transformers, no pickle, no remote code. Both packages are OPTIONAL (pyproject extra `embeddings`) and are
    imported lazily: nothing here needs them unless a model is loaded, so CI and the default tests run without them.
  * The model is pinned by name and sha256 (of the ONNX file and of the tokenizer file). `EmbeddingModel.load` refuses to
    run on a mismatch.
  * Fixed preprocessing (`PREPROCESS_VERSION`): the text is the item's canonical title exactly as the builder derives it
    (`canonical_title(title, config, host)`), the title only, no instruction prefix, truncated to `MAX_LENGTH` tokens,
    CLS-token pooling (the model's own 1_Pooling config), L2-normalised, float32.
  * Vectors are computed ONCE at ingestion and persisted with their provenance in `event_embeddings`. The build path
    reads stored vectors only (`EmbeddingStore.load_vectors`) and never calls the model: embeddings are not bitwise
    reproducible across hardware or runtime versions, so the stored vector, not the model, is the record.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from macro_engine import peer_paths
from macro_engine.events.builder import NewsItem, canonical_title, extract_publisher
from macro_engine.events.config import E1Config, load_e1_config

PREPROCESS_VERSION = "canon-title-cls-l2-v1"
MAX_LENGTH = 64            # tokens, including [CLS] and [SEP]; a headline is far shorter, so this truncates almost nothing
DEFAULT_MODEL_DIR = peer_paths.REPO_ROOT / "data" / "models"
TABLE = "event_embeddings"
PROVENANCE_COLUMNS = ("tokenizer_sha256", "runtime_version")


class EmbeddingError(RuntimeError):
    pass


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def item_text(item: NewsItem, config: E1Config | None = None) -> str:
    """The text that is embedded for an item: its canonical title as the builder derives it (host-aware)."""
    cfg = config or load_e1_config()
    _, host, _ = extract_publisher(item.title, item.source_url, cfg)
    return canonical_title(item.title, cfg, host)


@dataclass(frozen=True)
class ModelPin:
    """Name and hashes the model must have. `model_sha256` is the one persisted with every vector."""

    name: str
    model_sha256: str
    tokenizer_sha256: str
    model_file: str = "model.onnx"
    tokenizer_file: str = "tokenizer.json"


class EmbeddingModel:
    """The pinned ONNX model. Build with `EmbeddingModel.load`; embedding is deterministic on one machine to within the
    measured tolerance (scripts/events_embed.py --determinism), but the stored vector is the contract."""

    def __init__(self, pin: ModelPin, session: Any, tokenizer: Any, input_names: Sequence[str]) -> None:
        self.pin = pin
        self._session = session
        self._tok = tokenizer
        self._inputs = list(input_names)
        self.runtime_version = ""

    @property
    def provenance(self) -> dict[str, str]:
        """Recorded with every stored vector next to the model sha256 (P5a: the freeze manifest needs them)."""
        return {"tokenizer_sha256": self.pin.tokenizer_sha256, "runtime_version": self.runtime_version}

    @classmethod
    def load(cls, pin: ModelPin, model_dir: str | Path | None = None) -> EmbeddingModel:
        root = Path(model_dir) if model_dir is not None else DEFAULT_MODEL_DIR / pin.name
        model_path, tok_path = root / pin.model_file, root / pin.tokenizer_file
        for path, want, label in ((model_path, pin.model_sha256, "model"), (tok_path, pin.tokenizer_sha256, "tokenizer")):
            if not path.is_file():
                raise EmbeddingError(f"{label} file not found: {path}")
            got = file_sha256(path)
            if got != want:
                raise EmbeddingError(f"{label} sha256 mismatch for {path}: expected {want}, found {got}; refusing to run")
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:  # optional extra
            raise EmbeddingError("onnxruntime and tokenizers are needed (pip install -e .[embeddings])") from exc
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        opts.enable_cpu_mem_arena = False
        opts.enable_mem_pattern = False
        if hasattr(opts, "use_deterministic_compute"):
            opts.use_deterministic_compute = True
        session = ort.InferenceSession(str(model_path), opts, providers=["CPUExecutionProvider"])
        runtime_version = str(getattr(ort, "__version__", ""))
        tok = Tokenizer.from_file(str(tok_path))
        tok.enable_truncation(max_length=MAX_LENGTH)
        pad_id = tok.token_to_id("[PAD]")
        if pad_id is None:
            raise EmbeddingError("tokenizer has no [PAD] token")
        tok.enable_padding(pad_id=pad_id, pad_token="[PAD]")
        names = [i.name for i in session.get_inputs()]
        unknown = [n for n in names if n not in ("input_ids", "attention_mask", "token_type_ids")]
        if unknown:
            raise EmbeddingError(f"unexpected model inputs {unknown}")
        model = cls(pin, session, tok, names)
        model.runtime_version = runtime_version
        return model

    @property
    def name(self) -> str:
        return self.pin.name

    @property
    def sha256(self) -> str:
        return self.pin.model_sha256

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """(n, dim) float32, rows L2-normalised, in the order of `texts`."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        enc = self._tok.encode_batch([t if t else " " for t in texts])
        feeds = {
            "input_ids": np.array([e.ids for e in enc], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
            "token_type_ids": np.array([e.type_ids for e in enc], dtype=np.int64),
        }
        out = self._session.run(None, {k: v for k, v in feeds.items() if k in self._inputs})[0]
        cls_vec = np.asarray(out[:, 0, :], dtype=np.float32)
        norm = np.linalg.norm(cls_vec, axis=1, keepdims=True)
        return (cls_vec / np.where(norm > 0, norm, 1.0)).astype(np.float32)


# ----------------------------------------------------------------------------- the store


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class EmbeddingStore:
    """Additive table `event_embeddings(news_id, model_name, model_sha256, preprocess_version, dim, vector, embedded_at)`.
    One row per (news_id, model, sha256, preprocess version); the vector is dim little-endian float32 values.
    Columns `tokenizer_sha256` and `runtime_version` (onnxruntime) were added in P5a; older rows hold NULL there."""

    def __init__(self, con: Any, *, create: bool = True) -> None:
        """`create=False` for a read-only connection (the table must already exist)."""
        self.con = con
        if create:
            con.execute(
                f"CREATE TABLE IF NOT EXISTS {TABLE} (news_id VARCHAR, model_name VARCHAR, model_sha256 VARCHAR, "
                "preprocess_version VARCHAR, dim INTEGER, vector BLOB, embedded_at TIMESTAMP, "
                "PRIMARY KEY (news_id, model_name, model_sha256, preprocess_version))")
            for col in PROVENANCE_COLUMNS:
                con.execute(f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS {col} VARCHAR")

    def existing_ids(self, model_name: str, model_sha256: str, preprocess_version: str = PREPROCESS_VERSION) -> set[str]:
        rows = self.con.execute(
            f"SELECT news_id FROM {TABLE} WHERE model_name = ? AND model_sha256 = ? AND preprocess_version = ?",
            [model_name, model_sha256, preprocess_version]).fetchall()
        return {r[0] for r in rows}

    def put(self, news_ids: Sequence[str], vectors: np.ndarray, model_name: str, model_sha256: str,
            preprocess_version: str = PREPROCESS_VERSION, embedded_at: datetime | None = None,
            tokenizer_sha256: str | None = None, runtime_version: str | None = None) -> int:
        """Insert vectors that are not stored yet (never overwrites). Returns the number inserted."""
        when = embedded_at or _now()
        added = 0
        for nid, vec in zip(news_ids, vectors, strict=True):
            row = np.asarray(vec, dtype="<f4")
            cur = self.con.execute(
                f"INSERT OR IGNORE INTO {TABLE} (news_id, model_name, model_sha256, preprocess_version, dim, vector, "
                "embedded_at, tokenizer_sha256, runtime_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING news_id",
                [nid, model_name, model_sha256, preprocess_version, int(row.shape[0]), row.tobytes(), when,
                 tokenizer_sha256, runtime_version])
            added += len(cur.fetchall())
        return added

    def load_vectors(self, news_ids: Iterable[str] | None, model_name: str, model_sha256: str,
                     preprocess_version: str = PREPROCESS_VERSION) -> dict[str, np.ndarray]:
        """Stored vectors only; ids with no stored vector are absent from the result. `None` = every stored vector."""
        sql = (f"SELECT news_id, dim, vector FROM {TABLE} WHERE model_name = ? AND model_sha256 = ? "
               "AND preprocess_version = ?")
        out: dict[str, np.ndarray] = {}
        for nid, dim, blob in self.con.execute(sql, [model_name, model_sha256, preprocess_version]).fetchall():
            vec = np.frombuffer(bytes(blob), dtype="<f4").astype(np.float32)
            if vec.shape[0] != dim:
                raise EmbeddingError(f"stored vector for {nid} has {vec.shape[0]} values, dim says {dim}")
            out[nid] = vec
        if news_ids is None:
            return out
        wanted = set(news_ids)
        return {k: v for k, v in out.items() if k in wanted}


def vector_manifest(con: Any, model_name: str, model_sha256: str, news_ids: Iterable[str] | None = None,
                    preprocess_version: str = PREPROCESS_VERSION) -> dict[str, Any]:
    """sha256 manifest of the stored `event_embeddings` rows, for the freeze record (prereg section 8).

    The digest runs over the rows sorted by news_id: key columns, dim, the raw vector bytes, and the tokenizer sha256 and
    onnxruntime version when stored. `embedded_at` is left out (a wall clock, not part of the vector). `news_ids=None`
    covers every stored row of the model; otherwise only those ids (ids with no stored vector are listed as missing)."""
    have = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?", [TABLE]).fetchall()}
    sel = ", ".join(["news_id", "dim", "vector"] + [c if c in have else f"NULL AS {c}" for c in PROVENANCE_COLUMNS])
    rows = con.execute(
        f"SELECT {sel} FROM {TABLE} WHERE model_name = ? AND model_sha256 = ? AND preprocess_version = ? ORDER BY news_id",
        [model_name, model_sha256, preprocess_version]).fetchall()
    wanted = None if news_ids is None else set(news_ids)
    h = hashlib.sha256()
    tokenizers: set[str] = set()
    runtimes: set[str] = set()
    seen: set[str] = set()
    for nid, dim, blob, tok, rt in rows:
        if wanted is not None and nid not in wanted:
            continue
        seen.add(nid)
        if tok is not None:
            tokenizers.add(tok)
        if rt is not None:
            runtimes.add(rt)
        h.update("\x1f".join([nid, model_name, model_sha256, preprocess_version, str(dim), bytes(blob).hex(),
                              tok or "", rt or ""]).encode("utf-8"))
        h.update(b"\n")
    return {"model_name": model_name, "model_sha256": model_sha256, "preprocess_version": preprocess_version,
            "rows": len(seen), "missing": sorted(wanted - seen) if wanted is not None else [],
            "tokenizer_sha256": sorted(tokenizers), "onnxruntime_version": sorted(runtimes),
            "manifest_sha256": h.hexdigest()}


def embed_items(store: EmbeddingStore, model: EmbeddingModel, items: Sequence[NewsItem], *, batch_size: int = 32,
                config: E1Config | None = None) -> dict[str, int]:
    """Embed the items that have no stored vector yet (idempotent) and persist them. Returns counts."""
    cfg = config or load_e1_config()
    have = store.existing_ids(model.name, model.sha256)
    todo = [i for i in items if i.news_id not in have]
    stored = 0
    for k in range(0, len(todo), batch_size):
        batch = todo[k:k + batch_size]
        vecs = model.embed([item_text(i, cfg) for i in batch])
        prov = getattr(model, "provenance", {})
        stored += store.put([i.news_id for i in batch], vecs, model.name, model.sha256,
                            tokenizer_sha256=prov.get("tokenizer_sha256"), runtime_version=prov.get("runtime_version"))
    return {"requested": len(items), "already_stored": len(items) - len(todo), "embedded": len(todo), "stored": stored}


def dense_cosine(a: np.ndarray | None, b: np.ndarray | None) -> float | None:
    """Cosine of two stored (L2-normalised) vectors; None when either is missing."""
    if a is None or b is None:
        return None
    return float(np.dot(a, b))


def load_model_pin(path: str | Path | None = None) -> ModelPin:
    """The pinned model of config/events_v23.yaml (`dense:` section)."""
    import yaml

    from macro_engine.events.linker import DEFAULT_V23_PATH

    doc = yaml.safe_load(Path(path or DEFAULT_V23_PATH).read_text(encoding="utf-8"))["dense"]
    if doc.get("preprocess_version") != PREPROCESS_VERSION:
        raise EmbeddingError(f"config preprocess_version {doc.get('preprocess_version')!r} != code {PREPROCESS_VERSION!r}")
    return ModelPin(doc["model_name"], doc["model_sha256"], doc["tokenizer_sha256"])
