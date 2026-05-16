from __future__ import annotations

from macro_engine.news.config import NewsSourceDefinition
from macro_engine.news.ingest import load_local_csv_source
from macro_engine.news.schema import NewsItem


class LocalCsvNewsProvider:
    def __init__(self, source: NewsSourceDefinition) -> None:
        self.source = source

    def load(self) -> list[NewsItem]:
        return load_local_csv_source(self.source)
