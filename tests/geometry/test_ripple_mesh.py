"""Property tests for the ripple-mesh generator."""

from __future__ import annotations

import math

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


def make_project(
    ripples_per_turn: int = 8,
    amplitude_mm: float = 1.0,
    radius_mm: float = 20.0,
    height_mm: float = 20.0,
    rise_per_turn_mm: float = 0.5,
    segments_per_ripple: int = 6,
    profile: list[ProfilePoint] | None = None,
) -> Project:
    return Project(
        generator=GeneratorRef(id="ripple-mesh", version=1),
        shape=Shape(
            height_mm=height_mm,
            profile=profile or [ProfilePoint(height_fraction=0.0, radius_mm=radius_mm)],
        ),
        pattern=Pattern(ripples_per_turn=ripples_per_turn, amplitude_mm=amplitude_mm),
        path=PathParams(
            rise_per_turn_mm=rise_per_turn_mm,
            segments_per_turn=8,
            segments_per_ripple=segments_per_ripple,
        ),
        extrusion=ExtrusionParams(width_mm=0.45, height_mm=0.2),
    )


@pytest.fixture
def generator():
    return get_generator("ripple-mesh")


def points(steps):
    return [s for s in steps if type(s).__name__ == "Point"]


def test_radius_stays_within_silhouette_plus_amplitude(generator):
    project = make_project(radius_mm=20.0, amplitude_mm=1.0)
    radii = [math.hypot(s.x, s.y) for s in points(generator.generate(project))]
    assert min(radii) >= 20.0 - 1.0 - 1e-6
    assert max(radii) <= 20.0 + 1.0 + 1e-6
    # With 6 segments per ripple, samples land at sine phases 0/60/120/180/240/300,
    # so the sampled extrema reach sin(120 deg) = 0.866 of the amplitude — the true
    # peak falls between samples. The ripple must still swing both ways.
    assert min(radii) < 20.0 - 0.85
    assert max(radii) > 20.0 + 0.85


def test_extrema_reached_with_fine_sampling(generator):
    project = make_project(radius_mm=20.0, amplitude_mm=1.0, segments_per_ripple=24)
    radii = [math.hypot(s.x, s.y) for s in points(generator.generate(project))]
    # 24 segments per ripple: nearest sample within 7.5 deg of the peak.
    assert min(radii) <= 20.0 - 0.98
    assert max(radii) >= 20.0 + 0.98


def test_ripple_count_matches_pattern(generator):
    project = make_project(ripples_per_turn=8, rise_per_turn_mm=1.0, height_mm=10.0,
                           segments_per_ripple=24)
    pts = points(generator.generate(project))
    radii = [math.hypot(s.x, s.y) for s in pts]

    # Count local maxima of r within the first turn: one clean peak per ripple.
    # 24 segments per ripple resolves each peak well inside the turn.
    one_turn = 8 * 24 + 1  # points spanning [0, 1] turn inclusive
    radii = radii[:one_turn]
    peaks = sum(
        1
        for i in range(1, len(radii) - 1)
        if radii[i] > radii[i - 1] and radii[i] >= radii[i + 1]
    )
    assert peaks == 8


def test_phase_shift_offsets_pattern_between_turns(generator):
    project = make_project(ripples_per_turn=8, rise_per_turn_mm=1.0, segments_per_ripple=24)
    project.pattern.phase_shift_per_turn_deg = 180.0
    pts = points(generator.generate(project))

    # Same theta, one turn apart: a 180 deg per-turn phase shift flips the sine.
    # Choose t = 1/12 turn: 8*theta = 240 deg, plus the within-turn phase ramp
    # 180 deg * 1/12 = 15 deg, so phi_total = 255 deg.
    segs = 8 * 24
    i = segs // 12  # t = 1/12 turn
    offset_turn0 = math.hypot(pts[i].x, pts[i].y) - 20.0
    offset_turn1 = math.hypot(pts[segs + i].x, pts[segs + i].y) - 20.0
    assert offset_turn0 == pytest.approx(-offset_turn1, abs=1e-9)
    assert offset_turn0 == pytest.approx(math.sin(math.radians(255.0)), abs=1e-9)


def test_validate_rejects_radius_collapse(generator):
    errors = generator.validate(make_project(radius_mm=1.0, amplitude_mm=2.0))
    assert any("collapses through the axis" in e for e in errors)


def test_validate_rejects_zero_ripples(generator):
    errors = generator.validate(make_project(ripples_per_turn=0))
    assert any("ripples_per_turn" in e for e in errors)


def test_validate_rejects_zero_amplitude(generator):
    errors = generator.validate(make_project(amplitude_mm=0.0))
    assert any("amplitude_mm" in e for e in errors)


def test_variable_silhouette_respected(generator):
    profile = [
        ProfilePoint(height_fraction=0.0, radius_mm=30.0),
        ProfilePoint(height_fraction=1.0, radius_mm=10.0),
    ]
    project = make_project(
        profile=profile, height_mm=20.0, rise_per_turn_mm=2.0, amplitude_mm=1.0
    )
    pts = points(generator.generate(project))
    radii = [math.hypot(s.x, s.y) for s in pts]
    zs = [s.z for s in pts]
    # Envelope follows the linear silhouette: 30 - z +/- 1.
    for z, r in zip(zs, radii):
        assert (30.0 - z) - 1.0 - 1e-6 <= r <= (30.0 - z) + 1.0 + 1e-6
