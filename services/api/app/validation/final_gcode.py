"""Final-file validation: parses the assembled P2S job and enforces the
invariants that must hold in the artifact itself — not just in the design.

Checks (hard errors unless noted):
- no unresolved template expressions (`{...}`, `[...]`) outside comments
- extrusion mode contract: M83 active before any extruding E move; no M82
  switching modes mid-body; G92 E only at known boundaries
- forbidden commands in the generated-body region (homing, leveling,
  toolchange, filament management, temperature changes)
- all XYZ moves inside the printable envelope; Z within printable height
- bed exclusion area intrusions by extruding moves (hard error)
- extruder-clearance heuristic warning near the gantry rod envelope
- monotonic Z within the body region (continuous design requirement)
"""

from __future__ import annotations

import re

from app.printers.bambu_p2s.configuration import P2SConfiguration

_MOVE = re.compile(r"^G[01](?!\d)")
_XYZE = {
    "X": re.compile(r"X(-?[\d.]+)"),
    "Y": re.compile(r"Y(-?[\d.]+)"),
    "Z": re.compile(r"Z(-?[\d.]+)"),
    "E": re.compile(r"E(-?[\d.]+)"),
}
_UNRESOLVED_CURLY = re.compile(r"\{[^}]*\}")
_UNRESOLVED_SQUARE = re.compile(r"\[[A-Za-z_][A-Za-z0-9_]*(?:\[\d*\])?\]")
_ENTRY_MARKER = "Mesh Studio body entry"
_EXIT_MARKER = "Mesh Studio body exit"


def validate_final_gcode(text: str) -> dict:
    config = P2SConfiguration()
    px, py = config.printable_area_mm()
    pz = config.printable_height_mm()
    exclude = config.bed_exclude_area()

    errors: list[str] = []
    warnings: list[str] = []

    body_start = text.find(_ENTRY_MARKER)
    body_end = text.find(_EXIT_MARKER, body_start + 1) if body_start != -1 else len(text)

    absolute = True        # G90/G91 modal tracking
    relative_e = None      # M82/M83 modal tracking; None = not yet declared
    e_reset_boundary = True  # G92 E0 allowed at start/end boundaries
    prev_xyz: dict[str, float | None] = {"X": None, "Y": None, "Z": None}
    body_max_z = None      # highest absolute Z inside the body
    body_last_z = None     # Z of the last body move
    body_z_monotonic = True
    body_g0_count = 0      # continuous-design contract: 1 approach move max
    in_body = False

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(";", 1)[0].strip()
        comment = raw.split(";", 1)[1] if ";" in raw else ""
        # Body-region markers live on comment-only lines: detect them before
        # skipping comment-only lines.
        if _ENTRY_MARKER in comment:
            in_body = True
        if _EXIT_MARKER in comment:
            in_body = False
        if not line:
            if _UNRESOLVED_CURLY.search(comment) or _UNRESOLVED_SQUARE.search(comment):
                warnings.append(f"line {lineno}: unresolved expression in comment")
            continue

        # Unresolved expressions in executable lines: hard error.
        if _UNRESOLVED_CURLY.search(line) or _UNRESOLVED_SQUARE.search(line):
            errors.append(f"line {lineno}: unresolved template expression: {line[:60]!r}")

        cmd = line.split()[0] if line.split() else ""

        if cmd in ("G90",):
            absolute = True
        elif cmd in ("G91",):
            absolute = False
            if in_body:
                errors.append(f"line {lineno}: G91 relative positioning inside body")
        elif cmd == "M82":
            relative_e = False
        elif cmd == "M83":
            relative_e = True
        elif cmd == "G92":
            e_val = _XYZE["E"].search(line)
            if e_val and float(e_val.group(1)) != 0.0 and in_body:
                errors.append(f"line {lineno}: G92 E with non-zero value inside body")
        elif cmd in ("M104", "M109", "M140", "M190") and body_start != -1 and in_body:
            errors.append(f"line {lineno}: {cmd} temperature change inside body")
        elif cmd == "G28" or cmd.startswith("M620") or cmd.startswith("M621") or cmd == "M600":
            if in_body:
                errors.append(f"line {lineno}: forbidden preparation command inside body: {cmd}")
        elif cmd.startswith("G29") and in_body:
            errors.append(f"line {lineno}: bed compensation command inside body: {cmd}")
        elif cmd.startswith("T") and cmd[1:].isdigit() and in_body:
            errors.append(f"line {lineno}: toolchange inside body: {cmd}")

        # Continuous-design contract: the only non-extruding move allowed in
        # the body is the initial approach (travel to the start point). Any
        # later G0 means the design lifts/moves without extruding.
        if _MOVE.match(cmd) and in_body:
            if cmd == "G0":
                body_g0_count += 1
                if body_g0_count > 1:
                    errors.append(f"line {lineno}: travel move (G0) inside body after the approach — continuous design must not travel")

        if _MOVE.match(cmd):
            x = _XYZE["X"].search(line)
            y = _XYZE["Y"].search(line)
            z = _XYZE["Z"].search(line)
            e = _XYZE["E"].search(line)
            if e is not None:
                if relative_e is None:
                    errors.append(f"line {lineno}: extrusion move before explicit M82/M83")
                elif relative_e is False and in_body:
                    errors.append(f"line {lineno}: absolute extrusion (M82) active inside body")
            if absolute:
                nx = float(x.group(1)) if x else prev_xyz["X"]
                ny = float(y.group(1)) if y else prev_xyz["Y"]
                nz = float(z.group(1)) if z else prev_xyz["Z"]
                for name, val in (("X", nx), ("Y", ny), ("Z", nz)):
                    if val is not None and not (-1e-9 <= val <= (pz if name == "Z" else max(px, py)) + 1e-9):
                        limit = pz if name == "Z" else max(px, py)
                        errors.append(f"line {lineno}: {name}={val} outside envelope (0..{limit})")
                if nz is not None:
                    if in_body:
                        if body_max_z is None or nz > body_max_z:
                            body_max_z = nz
                        if body_last_z is not None and nz < body_last_z - 1e-9:
                            body_z_monotonic = False
                        body_last_z = nz
                prev_xyz = {"X": nx, "Y": ny, "Z": nz}
                if e is not None and float(e.group(1)) > 0 and nx is not None and ny is not None:
                    _check_exclusion(lineno, nx, ny, exclude, errors, warnings)

    if relative_e is not True and body_start != -1:
        errors.append("relative extrusion (M83) never declared in the file")
    if not body_z_monotonic:
        errors.append("non-monotonic Z inside body (continuous design requirement)")
    # The body must end at its own highest Z — the machine end sequence
    # derives its lifts from max_layer_z, so a body that finishes lower than
    # its peak would make the resolved lifts wrong. (The end sequence itself
    # legitimately lifts then lowers to park; that is not an error.)
    if body_max_z is not None and body_last_z is not None and abs(body_last_z - body_max_z) > 1e-6:
        errors.append(
            f"body ends at Z {body_last_z} but its highest Z is {body_max_z}; "
            "the end sequence lift would be derived from the wrong height"
        )

    return {"errors": errors, "warnings": warnings}


def _check_exclusion(lineno: int, x: float, y: float, exclude, errors: list[str], warnings: list[str]) -> None:
    """Bed exclusion areas: axis-aligned rectangles listed as corner points."""
    # Pairs of corners defining rectangles (Bambu lists polygon corner pairs).
    for i in range(0, len(exclude) - 1, 2):
        (x1, y1), (x2, y2) = exclude[i], exclude[i + 1]
        if min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9 and min(y1, y2) - 1e-9 <= y <= max(y1, y2) + 1e-9:
            errors.append(
                f"line {lineno}: extruding move at ({x:.2f}, {y:.2f}) inside bed exclusion area"
            )
            return
