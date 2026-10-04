"""WS-C theme discovery: the call has its own token ceiling, and a reply cut off by it is recorded as such.

2026-10-04: deepseek-v4-flash spent the news classifier's whole 2,048-token ceiling on reasoning and returned an empty
answer (finish_reason "length") on every failed Sunday since 2026-08-09; the error artifact recorded only "empty".
"""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("discover_themes", ROOT / "scripts" / "discover_themes.py")
dt = importlib.util.module_from_spec(spec)
sys.modules["discover_themes"] = dt
spec.loader.exec_module(dt)

AI = SimpleNamespace(model="deepseek-v4-flash", base_url="https://api.example", api_key_env="DEEPSEEK_API_KEY",
                     max_tokens=2048, truncation_retry_multiplier=2.0, max_retries=1, request_timeout_seconds=60)


class Resp:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


def body(content, finish="stop", completion=100, reasoning=60):
    return {"choices": [{"finish_reason": finish, "message": {"content": content}}],
            "usage": {"completion_tokens": completion, "completion_tokens_details": {"reasoning_tokens": reasoning}}}


def test_the_discovery_call_asks_for_its_own_ceiling_not_the_classifiers(monkeypatch):
    sent = []
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr("requests.post", lambda url, headers, json, timeout: sent.append(json) or Resp(body("{}")))
    content, meta = dt._call_deepseek("s", "u", AI, max_tokens=dt.DISCOVERY_MAX_TOKENS)
    assert sent[0]["max_tokens"] == dt.DISCOVERY_MAX_TOKENS == 16384
    assert content == "{}" and meta == {"finish_reason": "stop", "completion_tokens": 100, "reasoning_tokens": 60}


def test_a_reply_cut_off_by_the_ceiling_is_recorded_with_its_finish_reason_and_tokens(monkeypatch, tmp_path):
    calls = []

    def fake(system, user, ai_config, *, max_tokens=None):
        calls.append(max_tokens)
        return "", {"finish_reason": "length", "completion_tokens": max_tokens, "reasoning_tokens": max_tokens}

    monkeypatch.setattr(dt, "_call_deepseek", fake)
    err = tmp_path / "theme_discovery_error.json"
    try:
        dt._discover_candidates_with_retries(system="s", user="u", ai_config=AI, candidates_df=pd.DataFrame(),
                                             existing_theme_ids=set(), max_calls=2, error_output=str(err))
    except RuntimeError:
        pass
    else:
        raise AssertionError("an empty reply must not pass")
    assert calls == [dt.DISCOVERY_MAX_TOKENS, dt.DISCOVERY_RETRY_MAX_TOKENS]
    failures = json.loads(err.read_text(encoding="utf-8"))["failures"]
    assert [(f["finish_reason"], f["completion_tokens"], f["reasoning_tokens"]) for f in failures] == [
        ("length", 16384, 16384), ("length", 32768, 32768)]
