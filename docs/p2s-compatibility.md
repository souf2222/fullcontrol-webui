# Bambu Lab P2S compatibility report (Phase 0 spike)

**Date:** 2026-11-14 · **Upstream pinned:** BambuStudio `bf68eece` · **FullControl:** local clone at `588ecfe`

**Decision: Route A (native P2S job builder) is GO for continued development, with the
support status explicitly `statically_checked` — NOT hardware tested.** Route B
(patch a Bambu Studio-sliced reference job) stays a test-harness idea only.

Everything below is verifiable in this repo: the resolver, configuration
derivation, job assembly, and final-file validation are all exercised by
`tests/printers/` (23 tests) against verbatim upstream fixtures.

---

## 1. What was pinned and why

`services/api/app/printers/bambu_p2s/fixtures/upstream/` holds **verbatim,
byte-identical copies** (sha256-verified against upstream at build time; see
`fixtures/MANIFEST.json`) of 18 Bambu Studio profile files:

| Group | Files |
|---|---|
| machine model | `Bambu Lab P2S.json` (model N7, default bed: Textured PEI) |
| machine | `Bambu Lab P2S 0.4 nozzle.json`, `fdm_bbl_3dp_001_common.json`, `fdm_machine_common.json` |
| machine templates | machine_start / machine_end / layer_change / time_lapse / wrapping_detection / change_filament gcode |
| filament chain | `Bambu PLA Basic @BBL P2S` → `@base` → `fdm_filament_pla` → `fdm_filament_common` |
| process chain | `0.20mm Standard @BBL P2S` → `fdm_process_single_0.20` → `fdm_process_single_common` → `fdm_process_common` |

Any regeneration must re-verify hashes against upstream (the fetch script
`fetch_bambu_fixtures.py` walks the full `inherits` chain automatically).

## 2. Findings

| # | Finding | Consequence in code |
|---|---|---|
| 1 | The official P2S start template is a full program: 27 template variables, nested `{if}/{elsif}/{else}/{endif}`, multi-line conditions, `&&`/`‖`, subscripts like `[var[initial_no_support_extruder]]` | A whitelisted-AST resolver was required — string substitution is insufficient (`template.py`, fail-loud on unknown variables) |
| 2 | Official start uses **M83 relative extrusion itself** | Our relative-E contract matches the machine's own procedure |
| 3 | Start sequence runs `M211 X0 Y0 Z0` (soft endstops OFF) | Envelope enforcement is entirely our validator's job (it is: `final_gcode.py`) |
| 4 | Bed compensation (`G29.1` z-trim, `G29.2 S1`, partial `G29 A1`) is active before the body and must survive it | Body entry/exit blocks never touch G29; validator rejects G29 inside the body |
| 5 | P2S printable area 256×256 mm, height 256 mm, from the machine chain | Envelope check derives from fixtures, not constants |
| 6 | **`bed_exclude_area` is explicitly emptied by the P2S 0.4 profile** — the exclusion polygons in `fdm_bbl_3dp_001_common` (X1-series wiper zone) do not apply | Merged-chain value (empty) is authoritative; exclusion check stays in the validator for safety |
| 7 | Nearly all template variables are derivable: `textured_plate_temp_initial_layer=55`, `temperature_vitrification=45`, fan-layer settings, `filament_type=PLA` come from the filament chain | `configuration.py` derives them with provenance |
| 8 | A few variables are job-context (design-derived): `max_print_z`, `max_layer_z`, `first_layer_print_min/size` | Derived from the generated design after bed placement |
| 9 | The rest are declared assumptions: `curr_bed_type`, `overall_chamber_temperature=25` (cold start), `spiral_mode=1`, `initial_no_support_extruder=0` | Recorded in job metadata and diagnostics — visible, not hidden |
| 10 | `spiral_mode=1` makes the printer skip wrapping_detection/timelapse handling consistent with a single continuous wall | Declared for the one supported configuration |
| 11 | End template lifts to `{max_layer_z+0.4}`, then `{max_layer_z+10}`, parks high, then **lowers again** to park — final Z is legitimately below its own peak | Validator checks body-end Z equals body-max Z (not file-final Z) |
| 12 | P2S storage workflow (official FAQ): USB 2.0+, FAT32/exFAT, built-in storage; print files cannot be pushed from Bambu Studio to printer storage | Delivery is download-first; exact accepted extension set on P2S is **unverified** |
| 13 | Community reports plain `.gcode` works on Bambu machines via removable media; `.gcode.3mf` is the documented sliced-package format | We ship bare G-code for now; packaging deferred (open item) |

## 3. The critical FullControl integration finding

**A bare FullControl body prints nothing unless extrusion is turned on explicitly.**

FullControl's gcode writer emits `G0` for any Point while `state.extruder.on` is
falsy. With the default primer, the primer steps happen to switch extrusion on
(`front_lines_then_y` contains `Extruder(on=False)` then `Extruder(on=True)`),
but with `primer: "no_primer"` — which the P2S job builder needs, since the
official start template replaces priming — **nothing ever turns extrusion on**,
and the whole design exports as non-extruding travel moves.

Fix (all tested): generators emit an explicit state contract of their own —
`Extruder(on=False)` in the preamble (approach move is non-extruding), then
`Extruder(on=True)` immediately after the first Point. The body is now:
1 approach move + N continuously extruding `G1 ... E` moves. The final-file
validator enforces the same invariant on the assembled artifact (exactly one
`G0` inside the body; every other move extruding; Z monotonic).

This also fixed the same latent bug in the generic `/api/export/gcode` path.

## 4. Route A pipeline status

```
resolved official P2S start template   (bf68eece, resolved from fixtures)
+ explicit body-entry state contract   (M220/M221 reset, G90, M83)
+ FullControl print body               (bare: no FC start/end/primer)
+ explicit body-exit state contract
+ resolved official P2S end template   (lifts derived from design max Z)
```

- The **complete official start template resolves** — no placeholders left in
  executable lines (asserted in tests).
- Every template variable is fixture-derived, design-derived, or a recorded
  declaration. Unknown variable ⇒ hard error, never silent substitution.
- The resolver evaluates expressions with a whitelisted AST walker (no `eval`
  of template content).
- Final-file validation (on the assembled artifact itself): unresolved
  expressions, envelope bounds, exclusion areas, M82/M83 contract, forbidden
  preparation/toolchange/temperature commands in the body, monotonic body Z,
  body-end-Z == body-max-Z, continuous extrusion.

## 5. Scope of the one supported configuration

`configuration.SUPPORTED_CONFIG`:

| Field | Value | Status |
|---|---|---|
| Printer | Bambu Lab P2S | — |
| Nozzle | 0.4 mm | — |
| Material | Bambu PLA family (PLA Basic profile chain) | — |
| Bed | Textured PEI Plate | — |
| Colours/toolchanges | single, none | — |
| Support status | **statically_checked** | NOT hardware tested |

Anything else (other nozzles, materials, plates, multi-colour) is out of scope
until a configuration is put through the hardware checklist below.

## 6. Open items requiring hardware / user action

1. **P2S accepted file types** — test the generated bare G-code file from USB
   (FAT32) and record the result; community evidence says plain `.gcode` works
   on Bambu machines but the P2S is unconfirmed. If rejected, the `.gcode.3mf`
   packager becomes mandatory.
2. **Reference sliced job** — produce one known-good sliced `.gcode.3mf` from
   Bambu Studio (any small model, P2S 0.4 / PLA / Textured PEI, this profile
   revision) so we can inspect package contents and metadata before building
   the packager, and diff our resolved start block against Bambu Studio's.
3. **Record firmware version** of the P2S and Bambu Studio version at first
   hardware test — the "hardware tested" status must name these revisions.
4. **Supervised test sequence** (per plan): small spiral wall → small rippled
   wall → small mesh → taller mesh → lampshade scale, recording adhesion,
   strand formation, turn contact, sagging, motion smoothness, detection
   interruptions (M1015.3/.4 clog/air-printing detect are enabled — leave them
   intact and investigate false detections rather than disabling).
5. **Bambu Connect** acceptance of our artifact (later milestone).

## 7. Licenses

- **Bambu Studio profiles: AGPL-3.0.** The fixture files are verbatim copies
  with attribution in `MANIFEST.json`; if this app (or any distribution
  including them) is ever served/redistributed, AGPL obligations apply to the
  derived work. Do not modify fixture files in place.
- **FullControl: GPL-3.0** (local clone, used as a library).
- Generated G-code output contains resolved template text from these upstream
  files; that is an inherent consequence of Route A and is acceptable for a
  self-hosted tool, but should be re-examined before any SaaS distribution.
