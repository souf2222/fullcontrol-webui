"""Property tests for the smooth-spiral generator."""

from __future__ import annotations

import math

import pytest

from app.generators import get_generator
from app.schemas.project import (
    ExtrusionParams,
    GeneratorRef,
    GeneratorRef as GenRef,
    PathParams,
    Pattern,
    ProfilePoint,
    Project,
    Shape,
)


def make_project(
    height_mm: float = 30.0,
    radius_mm: float = 20.0,
    rise_per_turn_mm: float = 0.5,
    segments_per_turn: int = 48,
    profile: list[ProfilePoint] | None = None,
) -> Project:
    return Project(
        generator=GenRef(id="smooth-spiral", version=1),
        shape=Shape(
            height_mm=height_mm,
            profile=profile or [ProfilePoint(height_fraction=0.0, radius_mm=radius_mm)],
        ),
        path=PathParams(rise_per_turn_mm=rise_per_turn_mm, segments_per_turn=segments_per_turn),
        extrusion=ExtrusionParams(width_mm=0.45, height_mm=0.2),
    )


@pytest.fixture
def generator() -> object:
    return get_generator("smooth-spiral")


def test_registered(generator):
    assert generator is not None


def test_z_is_monotonic_and_spans_height(generator):
    project = make_project(height_mm=30.0, rise_per_turn_mm=0.5)
    steps = [s for s in generator.generate(project) if type(s).__name__ == "Point"]
    zs = [s.z for s in steps]

    assert zs[0] == pytest.approx(0.0, abs=1e-9)
    assert zs[-1] == pytest.approx(30.0, abs=1e-9)
    assert all(z2 >= z1 - 1e-9 for z1, z2 in zip(zs, zs[1:]))


def test_constant_silhouette_keeps_radius(generator):
    project = make_project(radius_mm=20.0)
    steps = [s for s in generator.generate(project) if type(s).__name__ == "Point"]
    radii = [math.hypot(s.x, s.y) for s in steps]
    assert max(abs(r - 20.0) for r in radii) < 1e-6


def test_variable_silhouette_follows_profile(generator):
    profile = [
        ProfilePoint(height_fraction=0.0, radius_mm=30.0),
        ProfilePoint(height_fraction=1.0, radius_mm=10.0),
    ]
    project = make_project(height_mm=20.0, rise_per_turn_mm=1.0, profile=profile)
    steps = [s for s in generator.generate(project) if type(s).__name__ == "Point"]
    zs = [s.z for s in steps]
    radii = [math.hypot(s.x, s.y) for s in steps]

    # Linear profile: radius decreases linearly with height.
    for z, r in zip(zs, radii):
        assert r == pytest.approx(30.0 - z, abs=1e-6)


def test_point_count_matches_sampling(generator):
    project = make_project(height_mm=30.0, rise_per_turn_mm=0.5, segments_per_turn=48)
    steps = [s for s in generator.generate(project) if type(s).__name__ == "Point"]
    # 30 / 0.5 = 60 turns x 48 segments = 2880 segments -> 2881 points.
    assert len(steps) == 2881


def test_validate_rejects_rise_over_height(generator):
    errors = generator.validate(make_project(height_mm=10.0, rise_per_turn_mm=20.0))
    assert any("at least one turn" in e for e in errors)
