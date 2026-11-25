"""Mesh Studio API (Phase 0 spike).

Endpoints:
    GET  /health              liveness
    GET  /api/generators      registered generator ids/versions
    POST /api/preview         project JSON -> toolpath preview payload
    POST /api/export/gcode    project JSON -> generic G-code (blocked on errors)
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from app import service
from app.generators import list_generators
from app.schemas.project import Project

app = FastAPI(
    title="Mesh Studio API",
    version="0.1.0",
    description="Parametric continuous-mesh toolpath generation built on FullControl.",
)

# Local development only (Vite dev server). Tighten before any deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/generators")
def generators() -> list[dict]:
    return list_generators()


@app.post("/api/preview")
def preview(project: Project) -> dict:
    return service.build_preview(project)


@app.post("/api/export/gcode")
def export_gcode(project: Project) -> Response:
    gcode, diagnostics, status = service.export_gcode(project)
    if status != 200:
        raise HTTPException(status_code=status, detail=diagnostics)
    return Response(
        content=gcode,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="mesh-studio-preview-generic.gcode"',
            "X-Validation-Warning": "generic-preview-profile",
        },
    )
