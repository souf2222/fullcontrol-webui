"""Tests for the P2S configuration derivation, job builder, and final-file
validator."""

from __future__ import annotations

import json
import pathlib
from copy import deepcopy

import pytest

from app.printers.bambu_p2s import job_builder
from app.printers.bambu_p2s.configuration import P2SConfiguration, SUPPORTED_CONFIG
from app.printers.bambu_p2s.template import TemplateError, TemplateResolver
from app.schemas.project import (
    ExtrusionParams,
    GeneratorRef,
    PathParams,
    Pattern,
    ProfilePoint,
    Project,
    Shape,
)
from app.validation.final_gcode import validate_final_gcode

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRESET_PATH = ROOT / "presets" / "lampshade_ripple_v1.json"


def small_spiral_project() -> Project:
    return Project(
        generator=GeneratorRef(id="smooth-spiral", version=1),
        name="spike specimen",
        shape=Shape(
            height_mm=12.0,
            profile=[ProfilePoint(height_fraction=0.0, radius_mm=15.0)],
        ),
        pattern=Pattern(ripples_per_turn=0, amplitude_mm=0.0),
        path=PathParams(rise_per_turn_mm=0.6, segments_per_turn=48),
        extrusion=ExtrusionParams(width_mm=0.45, height_mm=0.2),
        printer_profile_id=SUPPORTED_CONFIG["id"],
    )


def test_configuration_derives_template_values():
    config = P2SConfiguration()
    derived = config.derived_from_settings()
    # Textured PEI initial-layer bed temp for PLA: 55 from the filament chain.
    assert derived["bed_temperature_initial_layer_single"] == 55.0
    # min vitrification from temperature_vitrification (PLA chain = 45).
    assert derived["min_vitrification_temperature"] == 45.0
    assert derived["filament_type"] == ["PLA"]
    assert derived["nozzle_diameter"] == [0.4]
    # 220 - 80 must work in arithmetic: stored as numbers.
    assert derived["nozzle_temperature_initial_layer"] == [220.0]


def test_machine_geometry_from_fixtures():
    config = P2SConfiguration()
    assert config.printable_area_mm() == (256.0, 256.0)
    assert config.printable_height_mm() == 256.0
    # Upstream reality: the P2S 0.4 nozzle profile explicitly OVERRIDES the
    # common base's exclusion polygons with an empty list — the X1-series
    # wiper-zone exclusions do not apply to the P2S bed layout.
    assert config.bed_exclude_area() == []


def test_full_official_start_template_resolves():
    """Route A's core proof: the complete official P2S start template resolves
    with fixture-derived + declared + design-derived values."""
    config = P2SConfiguration()
    view = config.settings_view(
        config.design_derived(
            max_print_z=12.2,
            first_layer_min=(111.0, 111.0),
            first_layer_size=(34.0, 34.0),
        )
    )
    resolver = TemplateResolver(view, current_extruder=0)
    resolved = resolver.resolve(config.template("start"))
    # No unresolved expressions may remain.
    assert "{" not in resolved
    assert "[" not in resolved or "x" in resolved  # bed_exclude_area-like text
    # Expected resolved lines from branch evaluation:
    assert "M140 S55" in resolved                      # PLA bed temp
    assert "M145 P0" in resolved                       # cooling mode (chamber < 40)
    assert "M104 S140 A" in resolved                   # 220 - 80 pre-heat
    assert "M1015.3 S1 H0.4" in resolved               # clog detect for PLA
    assert "M1015.4 S1 K1 H0.4" in resolved            # E air printing detect
    assert "G29.1 Z0.01" in resolved                   # textured PEI z-trim
    assert "G29 A1 X111 Y111 I34 J34" in resolved      # partial bed leveling
    assert "set_filament_type:PLA" in resolved


def test_job_builder_produces_complete_job():
    gcode, diag = job_builder.build_p2s_job(small_spiral_project())
    assert diag["errors"] == []
    assert gcode is not None
    # Structure: resolved start, entry contract, body, exit, resolved end.
    assert ";======== P2S start gcode==========" in gcode
    assert "Mesh Studio body entry" in gcode
    assert "M83" in gcode                                    # relative E declared
    assert ";======== P2S end gcode ==========" in gcode
    # No unresolved expressions anywhere in executable lines.
    for line in gcode.splitlines():
        executable = line.split(";")[0]
        assert "{" not in executable, f"unresolved: {executable!r}"
    # Design placed and lifted: first layer at extrusion height, centred.
    assert "G29 A1 X" in gcode


def test_job_blocked_on_unknown_profile():
    bad = small_spiral_project()
    bad.printer_profile_id = "some-unvalidated-profile"
    gcode, diag = job_builder.build_p2s_job(bad)
    assert gcode is None
    assert any("unknown printer profile" in e for e in diag["errors"])


def test_final_validator_accepts_good_job():
    gcode, diag = job_builder.build_p2s_job(small_spiral_project())
    result = validate_final_gcode(gcode)
    assert result["errors"] == [], result["errors"]


def _inject_before_exit(good: str, snippet: str) -> str:
    """Insert a snippet as a proper line just before the body-exit marker."""
    lines = good.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if "Mesh Studio body exit" in line:
            return "".join(lines[:i]) + snippet + "".join(lines[i:])
    raise AssertionError("exit marker not found")


def test_final_validator_catches_violations():
    good, _ = job_builder.build_p2s_job(small_spiral_project())
    # 1: unresolved expression injected
    assert validate_final_gcode(good + "\nG1 X{max_layer_z} Y0\n")["errors"]
    # 2: out-of-envelope move
    assert validate_final_gcode(good + "\nG1 X300 Y0\n")["errors"]
    # 3: forbidden command inside body
    result = validate_final_gcode(_inject_before_exit(good, "G28 ; injected\n"))
    assert any("forbidden preparation command" in e for e in result["errors"])
    # 4: toolchange inside body
    assert any("toolchange" in e for e in validate_final_gcode(_inject_before_exit(good, "T1\n"))["errors"])
    # 5: bed-compensation command inside body
    assert any("bed compensation" in e for e in validate_final_gcode(_inject_before_exit(good, "G29 S1\n"))["errors"])
    # 6: travel move inside body after the approach (design break)
    assert any(
        "continuous design must not travel" in e
        for e in validate_final_gcode(_inject_before_exit(good, "G0 X150 Y150\n"))["errors"]
    )


def test_body_extrudes_continuously():
    """Regression: with `no_primer`, nothing else turns extrusion on — the
    body must be one approach move followed by extruding G1 moves."""
    gcode, _ = job_builder.build_p2s_job(small_spiral_project())
    body = gcode[gcode.find("Mesh Studio body entry"):gcode.find("Mesh Studio body exit")]
    move_lines = [l for l in body.splitlines() if l.startswith(("G0 ", "G1 "))]
    g0 = [l for l in move_lines if l.startswith("G0 ")]
    g1e = [l for l in move_lines if l.startswith("G1 ") and " E" in l]
    assert len(g0) == 1, "exactly one approach move expected"
    assert len(g1e) > 900, "continuous extruding body expected"
    assert not any(l.startswith("G1 ") and " E" not in l for l in move_lines), \
        "G1 without E inside body would indicate a broken extrusion state"


def test_end_template_lift_uses_design_max_z():
    gcode, _ = job_builder.build_p2s_job(small_spiral_project())
    # max_layer_z = 12.0 + 0.2 (placement lift) = 12.2; end lifts to 12.6/22.2
    assert "G1 Z12.6 F900" in gcode
    assert "G1 Z22.2 F900" in gcode


def test_presets_compatible_with_p2s_builder():
    """The Phase 0 preset must build end-to-end at reduced sampling; here we
    shrink sampling via the API schema to keep the test fast."""
    project = Project.model_validate(json.loads(PRESET_PATH.read_text()))
    project.shape.height_mm = 12.0
    project.path.rise_per_turn_mm = 0.6
    project.pattern.ripples_per_turn = 6
    project.path.segments_per_ripple = 6
    project.path.segments_per_turn = 8
    project.printer_profile_id = SUPPORTED_CONFIG["id"]
    gcode, diag = job_builder.build_p2s_job(project)
    assert diag["errors"] == []
    assert gcode is not None
    assert "M83" in gcode
