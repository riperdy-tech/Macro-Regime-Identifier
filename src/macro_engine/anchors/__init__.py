"""Capital-market anchors: the macro inputs a valuation engine cannot observe itself.

Three monthly, point-in-time anchors are published as ADDITIVE output artifacts:

    cost_of_capital_anchor.json    equity cost-of-capital level + risk-free decomposition
    long_run_growth_anchor.json    long-run nominal growth and the terminal-g it implies
    sector_multiple_bands.json     regime-conditional justified-multiple bands

Design rules that hold across the package:

* Deterministic and LLM-free, so every number is reproducible and auditable.
* A leg that cannot be measured is published as null with a stated reason. It is never
  back-filled with a plausible-looking constant.
* Provenance travels with the value: as-of date, observation date of every input, the
  scoring mode used, and every degradation.
"""

from macro_engine.anchors.config import AnchorConfig, load_anchor_config
from macro_engine.anchors.models import (
    AnchorBundle,
    AnchorProvenance,
    CostOfCapitalAnchor,
    LongRunGrowthAnchor,
    SectorMultipleBand,
    SectorMultipleBandsPayload,
)
from macro_engine.anchors.service import (
    COST_OF_CAPITAL_JSON,
    LONG_RUN_GROWTH_JSON,
    REPAIR_PACKAGE_JSON,
    SECTOR_MULTIPLE_BANDS_JSON,
    anchor_status,
    build_anchors,
    build_repair_package,
    write_anchor_outputs,
)

__all__ = [
    "COST_OF_CAPITAL_JSON",
    "LONG_RUN_GROWTH_JSON",
    "REPAIR_PACKAGE_JSON",
    "SECTOR_MULTIPLE_BANDS_JSON",
    "AnchorBundle",
    "AnchorConfig",
    "AnchorProvenance",
    "CostOfCapitalAnchor",
    "LongRunGrowthAnchor",
    "SectorMultipleBand",
    "SectorMultipleBandsPayload",
    "anchor_status",
    "build_anchors",
    "build_repair_package",
    "load_anchor_config",
    "write_anchor_outputs",
]
