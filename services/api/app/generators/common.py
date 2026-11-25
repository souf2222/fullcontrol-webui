"""Shared helpers for Mesh Studio generators.

Generators produce a list of FullControl steps from a validated Project.
Everything here is trusted server-side code; project files only carry data.
"""

from __future__ import annotations

import math
from typing import Callable

import fullcontrol as fc

from app.schemas.project import Project

#: Hard cap on generated points for one request. Protects the service from
#: pathological parameter combinations; full-resolution inspection designs
#: should stay well below this.
MAX_PREVIEW_POINTS = 500_000


class GenerationError(Exception):
    """Raised when generation cannot proceed; carries user-facing messages."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def silhouette_radius_fn(project: Project) -> Callable[[float], float]:
    """Linear interpolation of the silhouette profile over absolute height.

    Control points are given as (heightFraction, radiusMm); this returns
    R(z) in mm for z in [0, height_mm]. Linear interpolation is a deliberate
    Phase 0 simplification — a smooth (spline) option comes later.
    """
    shape = project.shape
    pts = sorted(shape.profile, key=lambda p: p.height_fraction)
    fractions = [p.height_fraction for p in pts]
    radii = [p.radius_mm for p in pts]

    def radius_at(z_mm: float) -> float:
        frac = max(0.0, min(1.0, z_mm / shape.height_mm))
        if frac <= fractions[0]:
            return radii[0]
        if frac >= fractions[-1]:
            return radii[-1]
        for i in range(1, len(pts)):
            f0, f1 = fractions[i - 1], fractions[i]
            if frac <= f1:
                span = f1 - f0
                w = 0.0 if span == 0 else (frac - f0) / span
                return radii[i - 1] + w * (radii[i] - radii[i - 1])
        return radii[-1]

    return radius_at


def preamble(project: Project) -> list:
    """State steps every design needs before any Point.

    Extrusion starts OFF: the first Point is the non-extruding approach move
    from the machine's current position. Generators must emit `extrusion_on()`
    after the first Point — with FullControl's `no_primer` primer, nothing
    else turns extrusion on (the built-in primers do it for their own moves).
    """
    extrusion = project.extrusion
    return [
        fc.Extruder(on=False, relative_gcode=True, dia_feed=extrusion.filament_diameter_mm),
        fc.ExtrusionGeometry(area_model="rectangle", width=extrusion.width_mm, height=extrusion.height_mm),
        fc.Printer(
            print_speed=extrusion.print_speed_mm_s * 60.0,   # FullControl speeds are mm/min
            travel_speed=extrusion.travel_speed_mm_s * 60.0,
        ),
    ]


def extrusion_on() -> list:
    """Turn extrusion on: subsequent Points emit G1 with extrusion."""
    return [fc.Extruder(on=True)]


def append_point(steps: list, r_mm: float, theta_rad: float, z_mm: float) -> None:
    """Append one polar-to-cartesian Point to the steps list."""
    steps.append(fc.Point(x=r_mm * math.cos(theta_rad), y=r_mm * math.sin(theta_rad), z=z_mm))


def check_point_budget(project: Project, segments_per_turn: int) -> None:
    """Raise GenerationError if the request would exceed MAX_PREVIEW_POINTS."""
    total_turns = project.shape.height_mm / project.path.rise_per_turn_mm
    n_points = math.ceil(total_turns * segments_per_turn) + 1
    if n_points > MAX_PREVIEW_POINTS:
        raise GenerationError([
            f"Requested design needs ~{n_points} points, exceeding the service cap of "
            f"{MAX_PREVIEW_POINTS}. Reduce height, increase rise_per_turn_mm, or reduce sampling."
        ])
