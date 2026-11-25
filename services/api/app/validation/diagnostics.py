"""Diagnostics: hard errors block export; warnings are heuristic and must be
treated as prompts for physical testing, not verdicts on printability.

Heuristic checks label contact/gap quality explicitly — FullControl has no
physical simulation of adhesion, sagging, or cooling.
"""

from __future__ import annotations

import math

import numpy as np

from app.schemas.project import Project

#: Above this point count, interactive use gets slow; suggest sampling down.
LARGE_PREVIEW_POINTS = 250_000


def validate_project(project: Project) -> tuple[list[str], list[str]]:
    """Pre-generation validation. Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []

    profile = sorted(project.shape.profile, key=lambda p: p.height_fraction)
    fractions = [p.height_fraction for p in profile]
    if len(set(fractions)) != len(fractions):
        errors.append("profile contains duplicate heightFraction values")
    elif fractions != sorted(fractions):
        errors.append("profile heightFraction values must be unique and ascending")
    if fractions and fractions[0] != 0.0:
        warnings.append("first profile point is not at heightFraction 0.0; radius is clamped below it")
    if fractions and fractions[-1] != 1.0:
        warnings.append("last profile point is not at heightFraction 1.0; radius is clamped above it")

    if project.pattern.ripples_per_turn == 0 or project.pattern.amplitude_mm == 0.0:
        warnings.append(
            "pattern produces no ripples (ripples_per_turn=0 or amplitude_mm=0); "
            "the result is a smooth spiral with no mesh openings"
        )
    return errors, warnings


def compute_stats(paths: list[dict], project: Project) -> dict:
    """Geometric stats from the serialized preview paths."""
    extruding_lengths: list[float] = []
    travel_lengths: list[float] = []

    for path in paths:
        pts = np.column_stack([path["x"], path["y"], path["z"]])
        if len(pts) < 2:
            continue
        seg_lengths = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        # Filter degenerate zero-length segments FullControl can emit.
        seg_lengths = seg_lengths[seg_lengths > 1e-9]
        (extruding_lengths if path["extruderOn"] else travel_lengths).extend(seg_lengths.tolist())

    ext_len = float(sum(extruding_lengths))
    trv_len = float(sum(travel_lengths))
    w = project.extrusion.width_mm
    h = project.extrusion.height_mm
    volume = ext_len * w * h  # rectangle area model approximation

    return {
        "pointCount": int(sum(len(p["x"]) for p in paths)),
        "pathCount": len(paths),
        "extrusionInterruptions": sum(1 for p in paths if not p["extruderOn"]),
        "extrusionPathLengthMm": round(ext_len, 3),
        "travelPathLengthMm": round(trv_len, 3),
        "extrusionVolumeMm3": round(volume, 3),
        "estimatedPrintSeconds": _estimate_seconds(ext_len, trv_len, project),
        "approximateTime": True,
        "turns": round(project.shape.height_mm / project.path.rise_per_turn_mm, 2),
    }


def _estimate_seconds(ext_len_mm: float, travel_len_mm: float, project: Project) -> float:
    return round(
        ext_len_mm / project.extrusion.print_speed_mm_s
        + travel_len_mm / project.extrusion.travel_speed_mm_s,
        1,
    )


def post_checks(project: Project, paths: list[dict], stats: dict) -> dict:
    """Checks that need the generated geometry. Returns full diagnostics."""
    errors, warnings = validate_project(project)

    # Non-finite coordinates anywhere in the generated path: hard failure.
    for path in paths:
        for axis in ("x", "y", "z"):
            vals = path[axis]
            if not all(math.isfinite(v) for v in vals):
                errors.append(f"non-finite {axis} coordinate generated in the toolpath")
                break

    # Continuous designs must not contain travel moves mid-path.
    if stats["extrusionInterruptions"] > 0:
        errors.append(
            f"unexpected extrusion interruptions ({stats['extrusionInterruptions']} travel path(s)) "
            "in a continuous-mode design"
        )

    if stats["pointCount"] > LARGE_PREVIEW_POINTS:
        warnings.append(
            f"{stats['pointCount']} points exceed the interactive preview threshold "
            f"({LARGE_PREVIEW_POINTS}); use reduced sampling while editing"
        )

    # --- Heuristic mesh-geometry notes (not printability verdicts) ---
    radius_fn_min = _min_silhouette_radius(project)
    rise = project.path.rise_per_turn_mm
    width = project.extrusion.width_mm

    vertical_gap = rise - width
    if vertical_gap > width:
        warnings.append(
            f"vertical gap between successive turns ({rise:.3f} mm rise vs {width:.3f} mm line "
            "width) is large; turn-to-turn contact may be weak — verify with a test print"
        )

    if project.pattern.ripples_per_turn > 0 and project.pattern.amplitude_mm > 0:
        n = project.pattern.ripples_per_turn
        circ_pitch = 2.0 * math.pi * radius_fn_min / n
        opening = circ_pitch - 2.0 * project.pattern.amplitude_mm
        if opening <= 0.0:
            warnings.append(
                "radial ripple peaks may overlap around the circumference "
                f"(circumferential pitch {circ_pitch:.3f} mm vs 2× amplitude "
                f"{2 * project.pattern.amplitude_mm:.3f} mm); mesh openings may be closed"
            )
        elif opening > 3.0 * width:
            warnings.append(
                f"circumferential openings are wide ({opening:.3f} mm vs {width:.3f} mm line "
                "width); ripple strands may not connect — verify with a test print"
            )

    return {
        "errors": errors,
        "warnings": warnings,
        "heuristics": ["gap and contact checks are geometric heuristics, not simulations of "
                       "adhesion, sagging, cooling, or strength"],
    }


def _min_silhouette_radius(project: Project) -> float:
    shape = project.shape
    fractions = sorted(p.height_fraction for p in shape.profile)
    by_fraction = {p.height_fraction: p.radius_mm for p in shape.profile}
    return min(by_fraction[f] for f in fractions)
