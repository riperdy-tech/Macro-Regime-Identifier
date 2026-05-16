from __future__ import annotations

from macro_engine.news.config import NewsSourceDefinition
from macro_engine.news.ingest import load_manual_text_source
from macro_engine.news.schema import NewsItem


class ManualTextNewsProvider:
    def __init__(self, source: NewsSourceDefinition) -> None:
        self.source = source

    def load(self) -> list[NewsItem]:
        return load_manual_text_source(self.source)
