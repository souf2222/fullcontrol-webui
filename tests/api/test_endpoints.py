"""Endpoint tests for the Mesh Studio API."""

from __future__ import annotations

import json
import pathlib
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRESET_PATH = ROOT / "presets" / "lampshade_ripple_v1.json"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def preset_project():
    return json.loads(PRESET_PATH.read_text())


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_generators_listed(client):
    gens = client.get("/api/generators").json()
    ids = {g["id"] for g in gens}
    assert {"smooth-spiral", "ripple-mesh"} <= ids


def test_preview_of_preset(client, preset_project):
    response = client.post("/api/preview", json=preset_project)
    assert response.status_code == 200
    payload = response.json()

    assert payload["generator"] == {"id": "ripple-mesh", "version": 1}
    assert payload["paths"], "expected generated paths"
    assert payload["stats"]["extrusionInterruptions"] == 0
    assert payload["diagnostics"]["errors"] == []
    assert payload["stats"]["turns"] == pytest.approx(120 / 0.4)

    # Bounding box reflects the silhouette (max radius 55 + amplitude 0.8).
    bb = payload["boundingBox"]
    assert bb["z"] == pytest.approx([0.0, 120.0], abs=1e-6)
    assert max(abs(v) for v in (bb["x"][0], bb["x"][1])) <= 55.8 + 1e-6


def test_preview_rejects_unknown_generator(client, preset_project):
    bad = deepcopy(preset_project)
    bad["generator"]["id"] = "not-a-generator"
    response = client.post("/api/preview", json=bad)
    assert response.status_code == 200
    payload = response.json()
    assert payload["paths"] == []
    assert any("unknown generator" in e for e in payload["diagnostics"]["errors"])


def test_preview_reports_geometry_errors(client, preset_project):
    bad = deepcopy(preset_project)
    bad["pattern"]["amplitudeMm"] = 60.0  # collapses through the axis
    response = client.post("/api/preview", json=bad)
    assert response.status_code == 200
    payload = response.json()
    assert payload["paths"] == []
    assert any("collapses" in e for e in payload["diagnostics"]["errors"])


def test_preview_rejects_schema_typos(client, preset_project):
    bad = deepcopy(preset_project)
    bad["shpe"] = preset_project["shape"]  # typo'd key must be forbidden
    response = client.post("/api/preview", json=bad)
    assert response.status_code == 422


def test_gcode_export_blocked_on_errors(client, preset_project):
    bad = deepcopy(preset_project)
    bad["pattern"]["amplitudeMm"] = 60.0
    response = client.post("/api/export/gcode", json=bad)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert any("collapses" in e for e in detail["errors"])


def test_gcode_export_unknown_profile_blocked(client, preset_project):
    bad = deepcopy(preset_project)
    bad["printerProfileId"] = "bambu-p2s-unvalidated"
    response = client.post("/api/export/gcode", json=bad)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert any("printer profile" in e for e in detail["errors"])


def test_gcode_export_round_trip(client, preset_project):
    response = client.post("/api/export/gcode", json=preset_project)
    assert response.status_code == 200
    gcode = response.text
    assert "G1" in gcode
    assert "; Printer name:" in gcode
    # Relative extrusion mode must be active for the design's E values.
    assert "M83" in gcode or "G91" in gcode
    move_lines = [line for line in gcode.splitlines() if line.startswith("G1 ") and "E" in line]
    assert len(move_lines) > 1000, "expected a long continuous extruding design"
