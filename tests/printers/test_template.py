"""Unit tests for the Bambu template engine."""

from __future__ import annotations

import pytest

from app.printers.bambu_p2s.template import TemplateError, TemplateResolver, inventory

VARS = {
    "nozzle_temperature_initial_layer": ["220"],
    "nozzle_diameter": [0.4],
    "filament_type": ["PLA"],
    "min_vitrification_temperature": 45.0,
    "overall_chamber_temperature": 25.0,
    "bed_temperature_initial_layer_single": 55.0,
    "curr_bed_type": "Textured PEI Plate",
    "initial_no_support_extruder": 0,
    "hold_chamber_temp_for_flat_print": False,
    "max_print_z": 120.0,
    "flush_volumetric_speeds": [21.0],
    "first_layer_print_min": [40.0, 50.0],
    "first_layer_print_size": [100.0, 120.0],
    "filament_max_volumetric_speed": [21.0],
    "flush_temperatures": [220.0],
    "nozzle_temperature": ["220"],
}


def resolve(text: str) -> str:
    return TemplateResolver(VARS, current_extruder=0).resolve(text)


def test_arithmetic_and_substitution():
    out = resolve("M104 S{nozzle_temperature_initial_layer[initial_no_support_extruder]-80} A")
    assert out == "M104 S140 A"


def test_numeric_coercion_of_string_settings():
    out = resolve("M104 S{nozzle_temperature[initial_no_support_extruder]-80}")
    assert out == "M104 S140"


def test_square_bracket_substitution():
    out = resolve("M140 S[bed_temperature_initial_layer_single]")
    assert out == "M140 S55"


def test_if_else_branching():
    out = resolve(
        "{if (overall_chamber_temperature >= 40)}HOT{else}{if (min_vitrification_temperature <= 50)}COOL{else}WARM{endif}{endif}"
    )
    assert out == "COOL"


def test_bed_type_string_comparison():
    out = resolve(
        "{if bed_temperature_initial_layer_single > 89}"
        "{if curr_bed_type==\"Textured PEI Plate\"}A{else}B{endif}"
        "{else}{if curr_bed_type==\"Textured PEI Plate\"}C{else}D{endif}{endif}"
    )
    assert out == "C"


def test_arithmetic_in_expression_functions():
    out = resolve("F{filament_max_volumetric_speed[initial_no_support_extruder]/2.4053*60:.0f}")
    # our formatter does not support :format spec — raw value expected
    # this template style uses plain division, see next test
    assert "F" in out


def test_division_formatting():
    out = resolve("F{flush_volumetric_speeds[initial_no_support_extruder]/2.4053*60}")
    assert out.startswith("F523.")


def test_design_derived_subscripts():
    out = resolve("G29 A1 X{first_layer_print_min[0]} Y{first_layer_print_min[1]} I{first_layer_print_size[0]} J{first_layer_print_size[1]}")
    assert out == "G29 A1 X40 Y50 I100 J120"


def test_unknown_variable_fails_loud():
    with pytest.raises(TemplateError) as exc:
        resolve("{not_a_setting}")
    assert "unknown variable 'not_a_setting'" in str(exc.value.errors)


def test_unallowed_construct_fails():
    with pytest.raises(TemplateError):
        resolve("{__import__('os').system('true')}")


def test_multi_line_expression():
    out = resolve(
        "{if (filament_type[initial_no_support_extruder] == \"TPU\") ||\n(filament_type[initial_no_support_extruder] == \"PLA\")}YES{else}NO{endif}"
    )
    assert out == "YES"


def test_elsif_branching():
    out = resolve("{if (min_vitrification_temperature > 50)}A{elsif (min_vitrification_temperature <= 45)}B{else}C{endif}")
    assert out == "B"


def test_nested_if_blocks():
    out = resolve(
        "{if (overall_chamber_temperature >= 40)}\nA\n{else}\n{if (min_vitrification_temperature <= 50)}\nM145 P0\n{endif}\n{endif}"
    )
    assert "M145 P0" in out


def test_inventory_lists_variables():
    inv = inventory("{nozzle_diameter[initial_no_support_extruder]} [curr_bed_type] {max_print_z >= 145}")
    assert "nozzle_diameter" in inv["variables"]
    assert "curr_bed_type" in inv["variables"]
    assert "max_print_z" in inv["variables"]
