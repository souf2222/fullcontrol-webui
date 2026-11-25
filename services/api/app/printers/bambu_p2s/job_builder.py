"""P2S job builder (Route A): resolved official machine sequences around a
FullControl-generated body.

    resolved P2S start template
    + explicit body-entry state contract
    + FullControl print body (movements only, bare of FC's own procedures)
    + explicit body-exit state contract
    + resolved P2S end template

Every template variable is resolved from pinned upstream fixtures, declared
assumptions, or design-derived metrics — an unresolved expression is a hard
error, never a placeholder left in the file. This module does NOT yet
produce the final .gcode.3mf package: packaging is a separate milestone that
requires a known-good sliced reference job to inspect (see
docs/p2s-compatibility.md).
"""

from __future__ import annotations

import math

import fullcontrol as fc

from app.printers.bambu_p2s import state_contract
from app.printers.bambu_p2s.configuration import P2SConfiguration, SUPPORTED_CONFIG
from app.printers.bambu_p2s.template import TemplateError, TemplateResolver
from app.schemas.project import Project
from app.validation.diagnostics import compute_stats, post_checks, validate_project
from app.generators import get_generator

#: Output feed rate for body travel moves comes from the project's extrusion
#: settings; FullControl emits F on the first move of each speed change.


def build_p2s_job(project: Project) -> tuple[str | None, dict]:
    """Returns (job_gcode_or_None, diagnostics). Blocked on any hard error."""
    errors, warnings = validate_project(project)
    generator = None
    if project.printer_profile_id not in (None, SUPPORTED_CONFIG["id"]):
        errors.append(
            f"unknown printer profile '{project.printer_profile_id}'; supported: "
            f"'{SUPPORTED_CONFIG['id']}' (status: {SUPPORTED_CONFIG['status']})"
        )
        project.printer_profile_id = None
    if not errors:
        generator = get_generator(project.generator.id)
        if generator is None:
            errors.append(f"unknown generator id '{project.generator.id}'")
        elif generator.version != project.generator.version:
            errors.append(f"generator version mismatch for '{project.generator.id}'")
        else:
            errors.extend(generator.validate(project))
    if errors or generator is None:
        return None, {"errors": errors, "warnings": warnings}

    steps = generator.generate(project)
    _place_on_bed(steps, project)
    body = _bare_body(steps)
    design_metrics = _design_metrics(steps)
    assumptions = {
        **P2SConfiguration().declared_assumptions(),
        **design_metrics,
    }

    try:
        job, template_errors = _assemble(project, body, design_metrics)
        errors.extend(template_errors)
    except TemplateError as exc:
        return None, {"errors": errors + exc.errors, "warnings": warnings}

    if errors:
        return None, {"errors": errors, "warnings": warnings}

    # Final-file validation on the assembled artifact itself.
    from app.validation.final_gcode import validate_final_gcode
    final_diag = validate_final_gcode(job)
    errors.extend(final_diag["errors"])
    warnings.extend(final_diag["warnings"])
    if errors:
        return None, {"errors": errors, "warnings": warnings}

    warnings.extend([
        "P2S support status: statically_checked — NOT hardware tested; supervised "
        "test prints (docs/p2s-compatibility.md) are required before use",
        "artifact is bare G-code; .gcode.3mf packaging is a separate milestone "
        "pending inspection of a known-good sliced reference job",
    ])
    diagnostics = {
        "errors": [],
        "warnings": warnings,
        "heuristics": ["gap/clearance checks are geometric heuristics, not printability verdicts"],
        "assumptions": assumptions,
        "supportStatus": SUPPORTED_CONFIG,
    }
    return job, diagnostics


def _assemble(project: Project, body: str, design_metrics: dict) -> tuple[str, list[str]]:
    config = P2SConfiguration()
    resolver = TemplateResolver(config.settings_view(design_metrics), current_extruder=0)
    errors: list[str] = []

    start = config.template("start")
    end = config.template("end")

    # The body-derived templates below are intentionally NOT included: a
    # continuous single-wall design has no layer changes and no toolchanges.
    # (layer_change/time_lapse/wrapping/change_filament)

    try:
        start_resolved = resolver.resolve(start)
        end_resolved = resolver.resolve(end)
    except TemplateError as exc:
        raise TemplateError(exc.errors) from exc

    entry = state_contract.ENTRY_BLOCK.format(
        design_name=project.name,
        generator=f"{project.generator.id} v{project.generator.version}",
    )
    header = (
        f"; Mesh Studio P2S job\n"
        f"; config: {SUPPORTED_CONFIG['id']} (status: {SUPPORTED_CONFIG['status']})\n"
        f"; template source commit: see MANIFEST.json\n"
    )
    job = "\n".join([
        header,
        start_resolved.strip(),
        entry,
        body.strip(),
        state_contract.EXIT_BLOCK,
        end_resolved.strip(),
        "",
    ])
    return job, errors


def _bare_body(steps: list) -> str:
    """Movement-only G-code from FullControl, without FC's own start/end
    procedures or primer. FullControl still emits the explicit M83 for the
    design's relative extrusion and F values on speed changes."""
    return fc.transform(
        steps,
        "gcode",
        fc.GcodeControls(
            printer_name="Community/Generic",
            save_as=None,
            include_date=False,
            initialization_data={
                "start_gcode": "; --- parametric body only (Mesh Studio) ---",
                "end_gcode": "",
                "primer": "no_primer",
                "print_speed_percent": 100,
                "material_flow_percent": 100,
            },
        ),
        show_tips=False,
    )


def _place_on_bed(steps: list, project: Project) -> None:
    """Default placement: centre the design on the printable area and raise
    Z so the first strand's nozzle height equals the extrusion height
    (material spans [z - h, z]; nozzle Z is the strand top). Explicit
    user-controlled placement arrives with the schema in a later phase and
    must respect the bed exclusion areas."""
    pts = [s for s in steps if type(s).__name__ == "Point" and s.x is not None]
    cx = (min(s.x for s in pts) + max(s.x for s in pts)) / 2.0
    cy = (min(s.y for s in pts) + max(s.y for s in pts)) / 2.0
    px, py = P2SConfiguration().printable_area_mm()
    dx, dy = px / 2.0 - cx, py / 2.0 - cy
    dz = project.extrusion.height_mm
    for s in pts:
        s.x += dx
        s.y += dy
        s.z += dz


def _design_metrics(steps: list) -> dict:
    """Design-derived variables the official templates reference (machine
    coordinates, after placement)."""
    pts = [s for s in steps if type(s).__name__ == "Point" and s.x is not None]
    max_z = max(s.z for s in pts)
    # First-layer bounds: points at or below the first extrusion height.
    first_pts = [s for s in pts if s.z <= max_z]  # all points; single "layer"
    min_x = min(s.x for s in first_pts)
    min_y = min(s.y for s in first_pts)
    size_x = max(s.x for s in first_pts) - min_x
    size_y = max(s.y for s in first_pts) - min_y
    return P2SConfiguration().design_derived(
        max_print_z=max_z,
        first_layer_min=(min_x, min_y),
        first_layer_size=(max(size_x, 1.0), max(size_y, 1.0)),
    )
