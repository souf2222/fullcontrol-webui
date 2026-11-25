"""P2S machine configuration, resolved from pinned upstream fixtures.

Loads the Bambu Studio profile inheritance chain stored verbatim under
fixtures/upstream/ (see MANIFEST.json for provenance and hashes) and derives
the variables the machine templates reference but which are produced by
Bambu Studio's slicing process, our generated design, or explicit declared
assumptions. Every derived or assumed value is recorded so the state handoff
is auditable.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "upstream"

#: The one configuration this profile version is defined against. Support
#: status is deliberately explicit (see docs/p2s-compatibility.md).
SUPPORTED_CONFIG = {
    "id": "bambu_p2s_0.4_pla_textured-pei",
    "printer_model": "Bambu Lab P2S",
    "nozzle_diameter_mm": 0.4,
    "material": "Bambu PLA Basic @BBL P2S (or equivalent PLA family)",
    "bed_type": "Textured PEI Plate",
    "status": "statically_checked",  # NOT hardware tested yet
}

_CHAINS = {
    "machine": [
        "machine/fdm_machine_common.json",
        "machine/fdm_bbl_3dp_001_common.json",
        "machine/Bambu Lab P2S 0.4 nozzle.json",
    ],
    "filament": [
        "filament/fdm_filament_common.json",
        "filament/fdm_filament_pla.json",
        "filament/Bambu PLA Basic @base.json",
        "filament/Bambu PLA Basic @BBL P2S.json",
    ],
    "process": [
        "process/fdm_process_common.json",
        "process/fdm_process_single_common.json",
        "process/fdm_process_single_0.20.json",
        "process/0.20mm Standard @BBL P2S.json",
    ],
}

_TEMPLATES = {
    "start": "machine/Bambu Lab P2S 0.4 nozzle template machine_start_gcode.json",
    "end": "machine/Bambu Lab P2S 0.4 nozzle template machine_end_gcode.json",
}

#: Bambu Studio derives these from the *selected bed type*; we replicate that
#: mapping for the one supported bed type.
_BED_TYPE_TO_TEMP_KEYS = {
    "Textured PEI Plate": ("textured_plate_temp", "textured_plate_temp_initial_layer"),
    "Cool Plate / Engineering Plate": ("eng_plate_temp", "eng_plate_temp_initial_layer"),
    "High Temp Plate": ("hot_plate_temp", "hot_plate_temp_initial_layer"),
    "Cool Plate": ("cool_plate_temp", "cool_plate_temp_initial_layer"),
    "Supertack Plate": ("supertack_plate_temp", "supertack_plate_temp_initial_layer"),
}


def _first_list_value(raw: Any) -> Any:
    if isinstance(raw, (list, tuple)):
        return raw[0] if raw else None
    return raw


def _to_number(raw: Any) -> float | None:
    v = _first_list_value(raw)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class P2SConfiguration:
    def __init__(self) -> None:
        self.chains: dict[str, dict[str, Any]] = {}
        for group, files in _CHAINS.items():
            merged: dict[str, Any] = {}
            for rel in files:
                merged.update(json.loads((FIXTURES / rel).read_text(encoding="utf-8")))
            self.chains[group] = merged
        self.machine = self.chains["machine"]
        self.filament = self.chains["filament"]
        self.process = self.chains["process"]

    # -- raw template access -------------------------------------------------
    def template(self, which: str) -> str:
        data = json.loads((FIXTURES / _TEMPLATES[which]).read_text(encoding="utf-8"))
        key = f"machine_{which}_gcode"
        return data[key]

    # -- derived variables ----------------------------------------------------
    def derived_from_settings(self) -> dict[str, Any]:
        """Settings the templates reference that Bambu Studio computes from
        profile values. Derived for the one supported configuration."""
        bed_type = SUPPORTED_CONFIG["bed_type"]
        temp_keys = _BED_TYPE_TO_TEMP_KEYS[bed_type]
        bed_initial = _to_number(self.filament.get(temp_keys[1], 0)) or 0.0

        nozzle_temp = _to_number(self.filament.get("nozzle_temperature")) or 0.0
        nozzle_initial = _to_number(self.filament.get("nozzle_temperature_initial_layer")) or 0.0
        max_vol_speed = _to_number(self.filament.get("filament_max_volumetric_speed")) or 0.0

        # Flush values for single-material single-colour: same material
        # before/after, so flush temp falls back to the nozzle temperature.
        flush_temp = _to_number(self.filament.get("filament_flush_temp"))
        if not flush_temp:
            flush_temp = nozzle_temp
        flush_vol = _to_number(self.filament.get("filament_flush_volumetric_speed"))
        if not flush_vol:
            flush_vol = max_vol_speed

        return {
            "bed_temperature_initial_layer_single": bed_initial,
            "bed_temperature_initial_layer": [bed_initial],
            "min_vitrification_temperature": _to_number(self.filament.get("temperature_vitrification")) or 0.0,
            "nozzle_temperature": [nozzle_temp],
            "nozzle_temperature_initial_layer": [nozzle_initial],
            "first_layer_temperature": [nozzle_initial],
            "nozzle_diameter": [SUPPORTED_CONFIG["nozzle_diameter_mm"]],
            "filament_type": [_first_list_value(self.filament.get("filament_type", "PLA"))],
            "filament_max_volumetric_speed": [max_vol_speed],
            "flush_temperatures": [flush_temp],
            "flush_volumetric_speeds": [flush_vol],
            "additional_cooling_fan_speed": _list_numbers(self.filament.get("additional_cooling_fan_speed")),
            "additional_fan_full_speed_layer": _list_numbers(self.filament.get("additional_fan_full_speed_layer")),
            "close_additional_fan_first_x_layers": _list_numbers(self.filament.get("close_additional_fan_first_x_layers")),
            "first_x_layer_fan_speed": _list_numbers(self.filament.get("first_x_layer_fan_speed")),
            "max_additional_fan": _to_number(self.filament.get("additional_cooling_fan_speed")) or 0.0,
        }

    def declared_assumptions(self) -> dict[str, Any]:
        """Values that would normally come from the runtime environment or
        the slicer's job context. These are ASSUMPTIONS, recorded in job
        metadata and surfaced in diagnostics — not sensor readings."""
        return {
            "initial_no_support_extruder": 0,
            "current_extruder": 0,
            "curr_bed_type": SUPPORTED_CONFIG["bed_type"],
            "overall_chamber_temperature": 25.0,   # cold start, no chamber heater
            "hold_chamber_temp_for_flat_print": False,  # P2S: no chamber heater
            "spiral_mode": 1,                      # continuous single-wall design
        }

    def design_derived(self, max_print_z: float, first_layer_min: tuple[float, float],
                       first_layer_size: tuple[float, float]) -> dict[str, Any]:
        return {
            "max_print_z": max_print_z,
            "max_layer_z": max_print_z,
            "first_layer_print_min": list(first_layer_min),
            "first_layer_print_size": list(first_layer_size),
        }

    def settings_view(self, design: dict[str, Any]) -> dict[str, Any]:
        """Full variable mapping for template resolution, with provenance."""
        view: dict[str, Any] = {}
        view.update(self.derived_from_settings())
        view.update(self.declared_assumptions())
        view.update(design)
        return view

    # -- machine geometry ------------------------------------------------------
    def printable_area_mm(self) -> tuple[float, float]:
        area = [a.split("x") for a in self.machine.get("printable_area", ["256x0", "256x256"])]
        xs = {float(p[0]) for p in area}
        ys = {float(p[1]) for p in area}
        return max(xs), max(ys)

    def printable_height_mm(self) -> float:
        return float(_first_list_value(self.machine.get("printable_height", 256)))

    def bed_exclude_area(self) -> list[tuple[float, float]]:
        pts = []
        for a in self.machine.get("bed_exclude_area", []):
            x, y = a.split("x")
            pts.append((float(x), float(y)))
        return pts


def _list_numbers(raw: Any) -> list[float]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    out = []
    for v in raw:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(0.0)
    return out
