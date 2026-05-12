from __future__ import annotations

from pathlib import Path

import pandas as pd


def read_parquet_table(path: str | Path) -> pd.DataFrame:
    return pd.read_parquet(path)
