# Embedding model provenance (events arm B)

Downloaded 2026-10-18 under the operator approval recorded in `BRIEF_P4C.md` (one small English sentence-embedding model
as ONNX plus its tokenizer, and the CPU runtime packages). Nothing else was downloaded.

## Model

| Field | Value |
| --- | --- |
| Model | `BAAI/bge-small-en-v1.5` (the preferred artifact; the MiniLM fallback was not needed) |
| Source | Hugging Face Hub, repository revision (commit) `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` (last modified 2024-02-22) |
| Local directory | `data/models/bge-small-en-v1.5/` (new, empty before the download; git-ignored through `.gitignore` `data/models/`; never committed) |
| Output | 384 dimensions; CLS-token pooling (the repository's own `1_Pooling/config.json`); we L2-normalise |
| Pinned in | `config/events_v23.yaml` (`dense:`); `EmbeddingModel.load` refuses to run on a sha256 mismatch |

Downloaded files, sha256 and size (URLs are `https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a/<path>`):

| Path | Bytes | sha256 |
| --- | --- | --- |
| `onnx/model.onnx` (stored as `model.onnx`) | 133,093,490 | `828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35` |
| `tokenizer.json` | 711,396 | `d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66` |
| `tokenizer_config.json` | 366 | `9261e7d79b44c8195c1cada2b453e55b00aeb81e907a6664974b4d7776172ab3` |
| `1_Pooling/config.json` | 190 | `d1caf60c96f5fba2157c0c26b76d80818fad6cf0b8eb5e73ec372ff9818eba5c` |

The `model.onnx` hash equals the Hub's own LFS sha256 for that file at that revision (read from the Hub API listing before
downloading). The `.bin` / `.safetensors` weights were not downloaded.

## The ONNX graph is plain

Checked by decoding the protobuf directly (no `onnx` package, nothing installed for the audit): one opset import
(`ai.onnx`, version 11), **no other operator domain, no local functions, 197 initializers and none uses external data**
(the whole model is the single file). The 19 operator types are all standard ONNX: Add, Cast, Concat, Constant, Div, Erf,
Gather, MatMul, Mul, Pow, ReduceMean, Reshape, Shape, Slice, Softmax, Sqrt, Sub, Transpose, Unsqueeze. No custom op, no
plugin library, no `trust_remote_code`, no pickle. The tokenizer is `tokenizers`' own JSON format (data, not code).

## Runtime packages (optional extra `embeddings`)

Installed into the repo venv with exact pins; declared in `pyproject.toml` under `[project.optional-dependencies].embeddings`;
**not** in `requirements.lock`, no CI or workflow file touched, imported lazily so CI and the default tests run without them
(the tests that need them skip).

| Package | Version | Note |
| --- | --- | --- |
| `onnxruntime` | 1.31.0 | CPU provider only; brought `flatbuffers 25.12.19`, `protobuf 7.36.2`, `packaging 26.2` (numpy already present) |
| `tokenizers` | 0.23.3 | installed with `--no-deps`: its optional dependency chain (`huggingface_hub` and its HTTP stack) is not used by `Tokenizer.from_file` and was not installed |

## Fixed preprocessing (`PREPROCESS_VERSION = canon-title-cls-l2-v1`)

Text = the item's canonical title as the builder derives it (`canonical_title(title, config, host)`, so an aggregator
" - Publisher" suffix is stripped exactly as it is for the report), title only, no instruction prefix; truncated to 64
tokens; padded to the longest in the batch; CLS pooling; L2 normalisation; float32 stored little-endian in
`event_embeddings(news_id, model_name, model_sha256, preprocess_version, dim, vector, embedded_at)`.
ONNX Runtime session: one intra-op thread, one inter-op thread, sequential execution, graph optimisation disabled, no CPU
memory arena or pattern reuse, `use_deterministic_compute` on. The build path reads stored vectors only.

## Measurements (2026-10-18, this machine, CPU)

* Embedding run: 1143 items (every item first seen 2026-10-08 .. 2026-10-18, containing the 944-item pool and the 300
  reference items) in 24.1 s, 1143 vectors stored (`event_embeddings`, dim 384); a second run stored 0 (idempotent).
* Determinism (`scripts/events_embed.py --determinism`, the 300 reference titles): two runs in the same order and batch size
  32, a shuffled order at batch size 7, and batch size 1 all gave **max abs difference 0.0** (bitwise equal here); a fresh
  process recomputing the 300 reference vectors also matched the stored ones at 0.0. This is a measurement on one machine
  and one runtime version, not a guarantee across hardware or onnxruntime versions: the stored vectors are the contract.

## Notes added 2026-10-18 (P5a)

* `pip check` reports `huggingface-hub` as missing for `tokenizers`: expected, because `tokenizers` was installed with
  `--no-deps` (above). `Tokenizer.from_file` does not use it. Not a defect.
* The tokenizer sha256 (`d241a60d...5c66`) and the onnxruntime version (`1.31.0` at install) are now stored beside each new
  vector (`event_embeddings.tokenizer_sha256`, `.runtime_version`; NULL for vectors written before P5a) and enter the
  vector manifest (`scripts/events_embed.py --manifest`).

