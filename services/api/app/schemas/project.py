"""Pydantic schemas for the versioned Mesh Studio project document (schema v1).

The JSON representation uses camelCase field names (via alias generator) and
rejects unknown fields (`extra="forbid"`) so typos in saved projects surface
as validation errors instead of being silently ignored.

Project files are data only. Nothing in here is ever executed.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class GeneratorRef(CamelModel):
    """Identifies which server-side generator produced the toolpath."""

    id: str
    version: int = Field(default=1, ge=1)


class ProfilePoint(CamelModel):
    """One silhouette control point: radius at a fractional height."""

    height_fraction: float = Field(ge=0.0, le=1.0)
    radius_mm: float = Field(gt=0.0, le=500.0)


class Shape(CamelModel):
    height_mm: float = Field(gt=0.0, le=1000.0)
    profile: list[ProfilePoint] = Field(min_length=1)


class Pattern(CamelModel):
    """Ripple pattern applied on top of the silhouette.

    ripples_per_turn = 0 or amplitude_mm = 0 degenerates to a smooth spiral
    (which also produces a warning from diagnostics).
    """

    ripples_per_turn: int = Field(default=0, ge=0, le=2000)
    amplitude_mm: float = Field(default=0.0, ge=0.0, le=100.0)
    phase_offset_deg: float = Field(default=0.0)
    phase_shift_per_turn_deg: float = Field(default=0.0)


class PathParams(CamelModel):
    """Spiral progression and sampling.

    rise_per_turn_mm is deliberately independent of extrusion height: for a
    simple vase-style spiral they are often equal, but mesh designs must be
    free to separate them.
    """

    rise_per_turn_mm: float = Field(gt=0.0, le=50.0)
    start_angle_deg: float = Field(default=0.0)
    segments_per_turn: int = Field(default=96, ge=8, le=20000)
    segments_per_ripple: int = Field(default=6, ge=3, le=200)


class ExtrusionParams(CamelModel):
    width_mm: float = Field(gt=0.0, le=5.0)
    height_mm: float = Field(gt=0.0, le=5.0)
    filament_diameter_mm: float = Field(default=1.75, gt=0.0, le=10.0)
    print_speed_mm_s: float = Field(default=40.0, gt=0.0, le=500.0)
    travel_speed_mm_s: float = Field(default=120.0, gt=0.0, le=1000.0)


class Project(CamelModel):
    """The editable project document. This is the primary format."""

    schema_version: int = Field(default=1, ge=1, le=1)
    name: str = Field(default="Untitled", max_length=200)
    generator: GeneratorRef
    shape: Shape
    pattern: Pattern = Field(default_factory=Pattern)
    path: PathParams
    extrusion: ExtrusionParams
    printer_profile_id: str | None = None
    notes: str | None = Field(default=None, max_length=5000)
