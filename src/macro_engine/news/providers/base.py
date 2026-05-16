from __future__ import annotations

from typing import Protocol

from macro_engine.news.schema import NewsItem


class NewsProvider(Protocol):
    def load(self) -> list[NewsItem]:
        """Load news items from a configured source."""
