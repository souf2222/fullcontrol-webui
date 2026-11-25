"""Machine-state contract at the boundaries of the generated body.

The P2S official start sequence changes positioning modes, enables relative
extrusion, and activates bed compensation (G29.1 z-trim / G29.2 S1). The
generated body must not inherit implicit modal state, and the machine's
compensation state must be preserved — these blocks make the handoff
explicit instead of incidental.
"""

ENTRY_BLOCK = """\
;==== Mesh Studio body entry: explicit state contract ====
; Design: {design_name}
; Generator: {generator}
M220 S100 ; reset feedrate override
M221 S100 ; reset flow override
G90 ; absolute XYZ positioning (explicit; not inherited)
M83 ; relative extrusion (explicit; matches generated design)
"""

EXIT_BLOCK = """\
;==== Mesh Studio body exit: explicit state contract ====
G90 ; absolute XYZ for the machine end sequence
"""

#: Commands the generated body must never contain. Machine preparation
#: (homing, leveling, filament handling, temperature control) belongs to the
#: resolved official sequences, not to the parametric path.
FORBIDDEN_IN_BODY = ("G28", "M600", "M620", "M621", "M104", "M140", "M109", "M190")
FORBIDDEN_IN_BODY_PREFIXES = ("G29", "T")  # bed-compensation / toolchange
