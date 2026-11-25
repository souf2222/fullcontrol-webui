"""FullControl adapter: transforms generated steps into preview payloads and
G-code, always through fc.transform so preview and export derive from the
exact same design.

Preview uses PlotControls(raw_data=True): FullControl runs its own state
machine and returns PlotData (paths of parallel coordinate arrays, one new
path per extruder on/off transition) without importing Plotly. We serialize
that for the browser renderer.
"""

from __future__ import annotations

import fullcontrol as fc

from app.generators.common import GenerationError


class PreviewGenerationError(GenerationError):
    pass


def steps_to_preview(steps: list) -> dict:
    """Serialize a generated design to the wire format for the 3D viewport.

    Path-level extrusion width/height are reported once per path (constant
    across the path in schema v1); per-point color arrays from FullControl
    are dropped — the client colors by height/sequence instead.
    """
    plot_data = fc.transform(steps, "plot", fc.PlotControls(raw_data=True), show_tips=False)

    paths = []
    for p in plot_data.paths:
        if not p.xvals:
            continue
        paths.append({
            "extruderOn": bool(p.extruder.on) if p.extruder is not None else False,
            "x": p.xvals,
            "y": p.yvals,
            "z": p.zvals,
            "widthMm": p.widths[0] if p.widths else None,
            "heightMm": p.heights[0] if p.heights else None,
        })

    bb = plot_data.bounding_box
    return {
        "schemaVersion": 1,
        "paths": paths,
        "boundingBox": {
            "x": [bb.minx, bb.maxx],
            "y": [bb.miny, bb.maxy],
            "z": [bb.minz, bb.maxz],
        },
    }


def steps_to_gcode(steps: list, project_name: str) -> str:
    """Generate printer-independent G-code via the generic printer profile.

    This is a Phase 0 debug/round-trip export. It is NOT a validated printer
    export: printer-specific output (Phase 5) requires a validated profile
    and is enforced in the export route.
    """
    return fc.transform(
        steps,
        "gcode",
        fc.GcodeControls(
            # Capital-C prefix routes through FullControl's printer library
            # (see fullcontrol/gcode/state.py); 'community/Generic' would be
            # treated as a singletool module name instead.
            printer_name="Community/Generic",
            save_as=None,
            include_date=False,
            initialization_data={"print_speed_percent": 100, "material_flow_percent": 100},
        ),
        show_tips=False,
    )
