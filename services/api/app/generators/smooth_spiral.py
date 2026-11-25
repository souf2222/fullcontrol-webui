"""Smooth spiral generator: continuous helix following the silhouette.

This is the baseline generator used for continuity testing and export
round-trips. Every successive turn rises by `rise_per_turn_mm`; the path is
one unbroken extruding polyline from the first layer to the top.
"""

from __future__ import annotations

import math

from app.generators.common import append_point, check_point_budget, extrusion_on, preamble, silhouette_radius_fn
from app.schemas.project import Project


class SmoothSpiralGenerator:
    id = "smooth-spiral"
    version = 1

    def validate(self, project: Project) -> list[str]:
        errors: list[str] = []
        if project.path.rise_per_turn_mm > project.shape.height_mm:
            errors.append("rise_per_turn_mm exceeds the total height; the spiral needs at least one turn")
        return errors

    def generate(self, project: Project) -> list:
        errors = self.validate(project)
        if errors:
            raise ValueError(f"invalid project for {self.id}: {errors}")

        shape = project.shape
        path = project.path

        segments_per_turn = path.segments_per_turn
        check_point_budget(project, segments_per_turn)

        radius_at = silhouette_radius_fn(project)
        total_turns = shape.height_mm / path.rise_per_turn_mm
        n_points = math.ceil(total_turns * segments_per_turn)

        steps = preamble(project)
        for i in range(n_points + 1):
            t = min(i / segments_per_turn, total_turns)
            z = t * path.rise_per_turn_mm
            theta = 2.0 * math.pi * t + math.radians(path.start_angle_deg)
            append_point(steps, radius_at(z), theta, z)
            if i == 0:
                steps.extend(extrusion_on())
        return steps
