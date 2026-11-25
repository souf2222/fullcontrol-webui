"""Ripple mesh generator: continuous spiral with a radial ripple pattern.

Starting parameterisation (proposed model, not a verified printable recipe):

    theta(t) = 2*pi*t + start_angle
    z(t)     = rise_per_turn_mm * t
    r(t)     = R(z(t)) + amplitude_mm * sin(ripples_per_turn * theta(t) + phi(t))
    phi(t)   = phase_offset + phase_shift_per_turn * t

The turn coordinate t runs continuously over the whole design, so the path
never lifts the extruder: silhouette, ripple, and phase all evolve along one
continuous polyline.

Whether the result is a *usable* mesh (openings that stay open, turns that
still contact each other) is a physical question this generator cannot
answer. Diagnostics report the relevant gap heuristics; real validation
happens with printed test specimens (see docs/architecture.md).
"""

from __future__ import annotations

import math

from app.generators.common import append_point, check_point_budget, extrusion_on, preamble, silhouette_radius_fn
from app.schemas.project import Project


class RippleMeshGenerator:
    id = "ripple-mesh"
    version = 1

    def validate(self, project: Project) -> list[str]:
        errors: list[str] = []
        if project.path.rise_per_turn_mm > project.shape.height_mm:
            errors.append("rise_per_turn_mm exceeds the total height; the spiral needs at least one turn")
        if project.pattern.ripples_per_turn == 0:
            errors.append("ripple-mesh requires ripples_per_turn >= 1 (use the smooth-spiral generator instead)")
        if project.pattern.amplitude_mm <= 0.0:
            errors.append("ripple-mesh requires amplitude_mm > 0 (use the smooth-spiral generator instead)")

        # Radius collapse: the ripple must never drive the path through the axis.
        radius_fn = silhouette_radius_fn(project)
        min_radius = min(radius_fn(f * project.shape.height_mm) for f in _profile_samples(project))
        if min_radius - project.pattern.amplitude_mm <= 0.0:
            errors.append(
                f"silhouette min radius ({min_radius:.3f} mm) minus amplitude_mm "
                f"({project.pattern.amplitude_mm:.3f} mm) collapses through the axis"
            )
        return errors

    def generate(self, project: Project) -> list:
        errors = self.validate(project)
        if errors:
            raise ValueError(f"invalid project for {self.id}: {errors}")

        shape = project.shape
        path = project.path
        pattern = project.pattern

        # Sampling: at least segments_per_turn, and enough resolution that each
        # ripple is drawn with segments_per_ripple segments.
        segments_per_turn = max(
            path.segments_per_turn,
            pattern.ripples_per_turn * path.segments_per_ripple,
        )
        check_point_budget(project, segments_per_turn)

        radius_at = silhouette_radius_fn(project)
        total_turns = shape.height_mm / path.rise_per_turn_mm
        n_points = math.ceil(total_turns * segments_per_turn)

        ripples = float(pattern.ripples_per_turn)
        phase_offset = math.radians(pattern.phase_offset_deg)
        phase_shift = math.radians(pattern.phase_shift_per_turn_deg)
        amplitude = pattern.amplitude_mm

        steps = preamble(project)
        for i in range(n_points + 1):
            t = min(i / segments_per_turn, total_turns)
            z = t * path.rise_per_turn_mm
            theta = 2.0 * math.pi * t + math.radians(path.start_angle_deg)
            phi = phase_offset + phase_shift * t
            r = radius_at(z) + amplitude * math.sin(ripples * theta + phi)
            append_point(steps, r, theta, z)
            if i == 0:
                steps.extend(extrusion_on())
        return steps


def _profile_samples(project: Project) -> list[float]:
    """Absolute heights (mm) at which to sample the silhouette for checks."""
    n = 64
    height = project.shape.height_mm
    return [i * height / n for i in range(n + 1)]
