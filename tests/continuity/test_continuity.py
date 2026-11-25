"""Continuity guarantees for generated designs.

The continuous-mesh concept depends on the path being genuinely continuous:
one polyline, extruder never off mid-design, sane segment lengths, no
non-finite coordinates. These tests hold for every registered generator.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.generators import get_generator
from app.schemas.project import (
    ExtrusionParams,
    GeneratorRef,
    PathParams,
    Pattern,
    ProfilePoint,
    Project,
    Shape,
)


def make_project(generator_id: str, **overrides) -> Project:
    defaults = dict(
        generator=GeneratorRef(id=generator_id, version=1),
        shape=Shape(
            height_mm=15.0,
            profile=[
                ProfilePoint(height_fraction=0.0, radius_mm=20.0),
                ProfilePoint(height_fraction=1.0, radius_mm=14.0),
            ],
        ),
        pattern=Pattern(ripples_per_turn=6, amplitude_mm=0.8),
        path=PathParams(rise_per_turn_mm=0.5, segments_per_turn=32, segments_per_ripple=8),
        extrusion=ExtrusionParams(width_mm=0.45, height_mm=0.2),
    )
    defaults.update(overrides)
    return Project(**defaults)


@pytest.mark.parametrize("generator_id", ["smooth-spiral", "ripple-mesh"])
def test_design_is_one_continuous_extruding_path(generator_id):
    generator = get_generator(generator_id)
    steps = generator.generate(make_project(generator_id))
    # Adapter-level check: exactly one path, extruder on.
    from app.fullcontrol_adapter import steps_to_preview
    payload = steps_to_preview(steps)
    paths = payload["paths"]
    assert len(paths) == 1
    assert paths[0]["extruderOn"] is True


@pytest.mark.parametrize("generator_id", ["smooth-spiral", "ripple-mesh"])
def test_no_non_finite_coordinates(generator_id):
    generator = get_generator(generator_id)
    pts = [s for s in generator.generate(make_project(generator_id)) if type(s).__name__ == "Point"]
    for s in pts:
        assert math.isfinite(s.x) and math.isfinite(s.y) and math.isfinite(s.z)


@pytest.mark.parametrize("generator_id", ["smooth-spiral", "ripple-mesh"])
def test_segment_lengths_are_bounded(generator_id):
    """No teleporting jumps: each segment must be plausibly small relative to
    the sampling density."""
    generator = get_generator(generator_id)
    pts = [s for s in generator.generate(make_project(generator_id)) if type(s).__name__ == "Point"]
    coords = np.array([(s.x, s.y, s.z) for s in pts])
    seg = np.linalg.norm(np.diff(coords, axis=0), axis=1)
    assert seg.min() >= 0.0
    assert seg.max() < 5.0, f"unreasonably long segment: {seg.max():.3f} mm"


@pytest.mark.parametrize("generator_id", ["smooth-spiral", "ripple-mesh"])
def test_diagnostics_report_no_interruptions(generator_id):
    from app.service import build_preview
    payload = build_preview(make_project(generator_id))
    assert payload["stats"]["extrusionInterruptions"] == 0
    assert payload["diagnostics"]["errors"] == []
