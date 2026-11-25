"""Request orchestration: the single pipeline shared by preview and export.

    project JSON -> generator -> FullControl steps -> preview / gcode

Preview and export always derive from the same generated steps; there is no
separate JavaScript-side geometry implementation.
"""

from __future__ import annotations

from app.fullcontrol_adapter import steps_to_gcode, steps_to_preview
from app.generators import Generator, get_generator
from app.generators.common import GenerationError
from app.schemas.project import Project
from app.validation.diagnostics import compute_stats, post_checks, validate_project

#: Profiles that may be used for G-code export in Phase 0. Real printer
#: profiles arrive in Phase 5 and must pass the documented validation
#: sequence before being listed here.
_ALLOWED_EXPORT_PROFILES: set[str | None] = {None}


def build_preview(project: Project) -> dict:
    """Generate the preview payload, or an error-only payload if validation fails."""
    errors, warnings = validate_project(project)
    generator = _resolve_generator(project, errors)
    if errors or generator is None:
        return _empty_preview(errors, warnings)

    try:
        steps = generator.generate(project)
    except GenerationError as exc:
        return _empty_preview(errors + exc.errors, warnings)

    payload = steps_to_preview(steps)
    stats = compute_stats(payload["paths"], project)
    payload["generator"] = {"id": generator.id, "version": generator.version}
    payload["stats"] = stats
    payload["diagnostics"] = post_checks(project, payload["paths"], stats)
    return payload


def export_gcode(project: Project) -> tuple[str | None, dict, int]:
    """Returns (gcode_or_None, diagnostics, http_status). Export is blocked on
    any hard error, unknown generator, or unvalidated printer profile."""
    errors, warnings = validate_project(project)
    generator = _resolve_generator(project, errors)

    if project.printer_profile_id not in _ALLOWED_EXPORT_PROFILES:
        errors.append(
            f"unknown printer profile '{project.printer_profile_id}'; export requires a "
            "profile that has passed the documented printer-validation sequence"
        )

    if errors or generator is None:
        return None, {"errors": errors, "warnings": warnings}, 409

    try:
        steps = generator.generate(project)
        gcode = steps_to_gcode(steps, project.name)
    except GenerationError as exc:
        return None, {"errors": errors + exc.errors, "warnings": warnings}, 409

    diagnostics = {
        "errors": [],
        "warnings": warnings + [
            "generic preview profile only: printer-specific G-code requires a validated "
            "profile (Phase 5); inspect this file before attempting any print"
        ],
    }
    return gcode, diagnostics, 200


def _resolve_generator(project: Project, errors: list[str]) -> Generator | None:
    generator = get_generator(project.generator.id)
    if generator is None:
        errors.append(f"unknown generator id '{project.generator.id}'")
        return None
    if generator.version != project.generator.version:
        errors.append(
            f"generator '{project.generator.id}' supports version {generator.version}, "
            f"project requests version {project.generator.version}"
        )
        return None
    gen_errors = generator.validate(project)
    if gen_errors:
        errors.extend(gen_errors)
        return None
    return generator


def _empty_preview(errors: list[str], warnings: list[str]) -> dict:
    return {
        "schemaVersion": 1,
        "generator": None,
        "paths": [],
        "boundingBox": None,
        "stats": None,
        "diagnostics": {"errors": errors, "warnings": warnings, "heuristics": []},
    }
