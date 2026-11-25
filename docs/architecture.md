# Mesh Studio — Architecture

A web-based parametric toolpath editor for continuous-mesh designs, built on
**FullControl** as a pinned dependency. This document records the decisions
made so far; update it as phases land.

## Relationship to FullControl

- FullControl is used as a library, never forked or edited in place.
- In this repository layout, the dependency is the local clone itself
  (`fullcontrol/`, `lab/` at the repo root), installed editable into the API
  venv. The upstream state this repo is built against is **commit `588ecfe`
  ("Updated contents to put infinaxis into lab…")** — record any upstream
  update here when rebasing.
- All geometry runs through `fc.transform(steps, 'plot'|'gcode', controls)`.
  Preview and G-code export derive from the *same generated steps*; there is
  no duplicate geometry implementation.
- Upstream warns its internal structure may change; all FullControl usage is
  contained in `services/api/app/generators/common.py` and
  `services/api/app/fullcontrol_adapter/` so upstream changes are absorbed
  in one place.

## Phase 0 status (this milestone)

Proven, with tests:

1. **Two generators** producing FullControl steps from a versioned project
   document:
   - `smooth-spiral` (v1): continuous helix following the silhouette.
   - `ripple-mesh` (v1): continuous spiral with radial ripple pattern,
     parameterised by a continuous turn coordinate `t`:
     `theta = 2·pi·t`, `z = rise·t`, `r = R(z) + A·sin(n·theta + phi(t))`.
     This is a proposed starting model, **not** a verified printable mesh
     recipe. Printability is a physical question answered by test prints.
2. **Preview pipeline**: `PlotControls(raw_data=True)` runs FullControl's own
   state machine and returns `PlotData` (paths with parallel coordinate
   arrays, one path per extruder on/off transition) without importing Plotly.
   Serialized to JSON for a future browser renderer.
3. **G-code round trip**: `Community/Generic` printer profile (note the
   capital-C prefix: `fullcontrol/gcode/state.py` routes on it). Output
   marked with `X-Validation-Warning: generic-preview-profile`; it is a debug
   export, not a validated printer export.
4. **Validation**: hard errors (radius collapse through the axis, non-finite
   coordinates, unexpected extrusion interruptions, unknown generator/profile)
   block export; warnings (wide circumferential openings, large vertical turn
   gaps, point-count thresholds) are explicitly labelled geometric heuristics.

## Layout

```
services/api/          FastAPI service (package: app)
  app/generators/      trusted server-side generator code + registry
  app/fullcontrol_adapter/  the only place that touches fc.transform
  app/printers/        printer backends; bambu_p2s/ holds the Route A job
                       builder, template resolver, state contract, and pinned
                       verbatim upstream fixtures (fixtures/MANIFEST.json)
  app/validation/      diagnostics + final-file validation (errors block
                       export; warnings inform)
  app/schemas/         versioned project document (schema v1, camelCase JSON,
                       extra="forbid" so typos fail loudly)
presets/               sample projects (data only, never executed)
tests/                 geometry / continuity / api / printers suites
```

## Project format

The project document is the primary format (see `presets/lampshade_ripple_v1.json`).
Key rules:

- `schemaVersion` and `generator.id` + `generator.version` are mandatory;
  unknown generator ids or unsupported versions are rejected.
- `path.risePerTurnMm` is independent of `extrusion.heightMm` by design.
- Projects are **data only**. Uploaded projects must never execute Python;
  generators are trusted server-side code. (FullControl's own printer
  `import_printer` uses `eval()` on start/end G-code templates — another
  reason uploads stay away from printer-definition code.)

## Running

```bash
uv venv .venv
uv pip install --python .venv/bin/python -e services/api
.venv/bin/python -m pytest tests/geometry tests/continuity tests/api
cd services/api && ../../.venv/bin/uvicorn app.main:app --port 8100
```

Note: if your shell has a stale `VIRTUAL_ENV`, `uv pip install` targets the
wrong environment — pass `--python .venv/bin/python` explicitly.

## P2S printer profile (Phase 0 spike complete)

FullControl itself ships **no P2S/P1S/A1 profile** (its `community` library has
only a `bambulab_x1.py` reference). The P2S route therefore does not use a
FullControl printer definition at all: the official Bambu Studio machine
templates are resolved directly from pinned upstream fixtures.

Status and details live in **`docs/p2s-compatibility.md`**. Summary:

- Route A (native job builder) is implemented and tested: resolved official
  start/end templates + explicit state contract + bare FullControl body.
  Support status: `statically_checked`, NOT hardware tested.
- Bare-body export requires explicit extrusion control: FullControl emits
  non-extruding `G0` for every Point unless `Extruder(on=True)` state was set
  (the default primer sets it; `no_primer` does not). Generators now emit
  `Extruder(on=False)` + post-approach `Extruder(on=True)`; the final-file
  validator enforces one approach move + continuous extrusion on the artifact.
- Export for `printerProfileId: "bambu_p2s_0.4_pla_textured-pei"` produces the
  assembled P2S job; `null` keeps the generic debug export. Unknown ids are
  rejected.

## Known simplifications

- Silhouette interpolation is linear (spline option deferred).
- Extrusion volume estimate assumes the rectangle area model over path length.
- Time estimate is length/speed — labelled approximate in the payload.
- Preview is full-resolution only; interactive down-sampling arrives with the
  frontend (Phase 1) — the API payload is already ~1.9 MB for the preset.
- P2S job bed placement is fixed (centred at 128,128; Z lifted by extrusion
  height); explicit user placement is a later schema addition and must respect
  the exclusion areas (which are empty for the P2S per upstream, but the
  validator keeps the check).
- The P2S artifact is bare G-code; `.gcode.3mf` packaging is deferred pending
  inspection of a known-good sliced reference job.
- The first body move is a non-extruding approach from FullControl's virtual
  origin assumption (absolute G0 to the design start); a proper wipe/approach
  procedure is part of later physical validation.
