"""S1.4 (P0_0 §2.6) — the declarative composition registry.

`config/dimension_composition.yaml` states, per dimension, the ordered set of features that
are DECLARED valid over a date range. This is the fix for a general silent re-specification:
coverage-gated renormalization (`dimensions/scoring.py`) means any feature that enters or
leaves the valid set changes the effective weights of its dimension without any record (the
high-yield spread's ICE rolling licence window, S0.2, is the motivating case). The registry
makes that declared, not inferred: a feature that validates outside its declared window
invalidates the dimension for that date with reason `undeclared_composition:extra:<feature>`,
so a future silent re-specification fails loud instead of quietly changing history.

This module only loads and validates the registry and answers "what is declared for
(dimension, date)". The runtime check lives in `dimensions/scoring.py`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from macro_engine.dimensions.config import DimensionDefinition


class CompositionFeature(BaseModel):
    feature_id: str
    # Duplicated from phase_b_sources.yaml's dimension.features weight, not read from it, so a
    # drift between the two files is a loud config error (`validate_against_dimensions`) rather
    # than a silent one.
    weight: float = Field(gt=0)


class CompositionSegment(BaseModel):
    composition_id: str
    valid_from: date
    valid_to: date | None = None
    features: list[CompositionFeature]

    @model_validator(mode="after")
    def _valid_range(self) -> CompositionSegment:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError(
                f"{self.composition_id}: valid_to {self.valid_to} is before valid_from {self.valid_from}"
            )
        if not self.features:
            raise ValueError(f"{self.composition_id}: must declare at least one feature")
        return self

    def covers(self, on_date: date) -> bool:
        if on_date < self.valid_from:
            return False
        if self.valid_to is not None and on_date > self.valid_to:
            return False
        return True

    def feature_ids(self) -> set[str]:
        return {feature.feature_id for feature in self.features}


class DimensionComposition(BaseModel):
    dimension_id: str
    segments: list[CompositionSegment]

    @model_validator(mode="after")
    def _segments_ordered_and_disjoint(self) -> DimensionComposition:
        if not self.segments:
            raise ValueError(f"dimension {self.dimension_id}: must declare at least one composition segment")
        ordered = sorted(self.segments, key=lambda segment: segment.valid_from)
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier.valid_to is None or earlier.valid_to >= later.valid_from:
                raise ValueError(
                    f"dimension {self.dimension_id}: composition segments {earlier.composition_id!r} "
                    f"and {later.composition_id!r} overlap or are unordered"
                )
        return self

    def segment_for(self, on_date: date) -> CompositionSegment | None:
        for segment in self.segments:
            if segment.covers(on_date):
                return segment
        return None


class CompositionRegistry(BaseModel):
    dimensions: dict[str, DimensionComposition]

    def restricted_to(self, dimension_ids: set[str]) -> CompositionRegistry:
        """A registry with entries for dimensions outside `dimension_ids` dropped.

        `config/dimension_composition.yaml` describes the full production dimension set;
        a caller that loads a narrower `phase_b_sources.yaml` variant (a test fixture, an
        alternate deployment) legitimately configures only some of those dimensions. An
        entry for a dimension the caller never configured is not a drift to catch -- it is
        simply not applicable to this run, so it is dropped before validation rather than
        raising `unknown dimension_id` on every partial config.
        """
        return CompositionRegistry(
            dimensions={
                dimension_id: composition
                for dimension_id, composition in self.dimensions.items()
                if dimension_id in dimension_ids
            }
        )

    def declared_feature_ids(self, dimension_id: str, on_date: date) -> set[str] | None:
        """The declared valid feature set for (dimension_id, on_date).

        None means this dimension carries no declared composition (not registered), which
        means the runtime check is a no-op for it -- registration is additive, not required
        of every dimension in one commit.
        """
        composition = self.dimensions.get(dimension_id)
        if composition is None:
            return None
        segment = composition.segment_for(on_date)
        return None if segment is None else segment.feature_ids()

    def composition_id(self, dimension_id: str, on_date: date) -> str | None:
        composition = self.dimensions.get(dimension_id)
        if composition is None:
            return None
        segment = composition.segment_for(on_date)
        return None if segment is None else segment.composition_id


def load_composition_registry(path: str | Path) -> CompositionRegistry:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    raw = data.get("dimension_compositions", {}) or {}
    dimensions = {
        dimension_id: DimensionComposition(
            dimension_id=dimension_id,
            segments=[CompositionSegment.model_validate(segment) for segment in segments],
        )
        for dimension_id, segments in raw.items()
    }
    return CompositionRegistry(dimensions=dimensions)


def validate_registry_against_dimensions(
    registry: CompositionRegistry,
    dimensions: list[DimensionDefinition],
) -> None:
    """Fail loudly at config-load time, not at run time, on a drift between the two files."""
    dimension_lookup = {dimension.dimension_id: dimension for dimension in dimensions}
    for dimension_id, composition in registry.dimensions.items():
        if dimension_id not in dimension_lookup:
            raise ValueError(
                f"config/dimension_composition.yaml references unknown dimension_id {dimension_id!r}"
            )
        configured = {
            feature.feature_id: feature.weight for feature in dimension_lookup[dimension_id].features
        }
        for segment in composition.segments:
            for feature in segment.features:
                if feature.feature_id not in configured:
                    raise ValueError(
                        f"{segment.composition_id}: feature {feature.feature_id!r} is declared but "
                        f"not configured on dimension {dimension_id!r}"
                    )
                configured_weight = configured[feature.feature_id]
                if abs(configured_weight - feature.weight) > 1e-9:
                    raise ValueError(
                        f"{segment.composition_id}: feature {feature.feature_id!r} declares weight "
                        f"{feature.weight}, but phase_b_sources.yaml configures {configured_weight}"
                    )
