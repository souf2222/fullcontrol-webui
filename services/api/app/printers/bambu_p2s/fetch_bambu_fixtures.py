"""One-off fixture fetcher for the P2S compatibility spike (Phase 0).

Recursively downloads an upstream Bambu Studio profile and its full
`inherits` chain at a pinned revision, stores verbatim copies under
fixtures/upstream/, and writes MANIFEST.json with source URLs and SHA-256
hashes. Upstream profiles are AGPL-3.0; these are unmodified verbatim
copies with attribution (see docs/p2s-compatibility.md).

Usage:  python fetch_bambu_fixtures.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import urllib.parse
import urllib.request

COMMIT = "bf68eece"
BASE = f"https://raw.githubusercontent.com/bambulab/BambuStudio/{COMMIT}/resources/profiles/BBL"
FIXTURES = pathlib.Path(__file__).parent / "upstream"

HEADERS = {"User-Agent": "mesh-studio-spike (fixture fetch; verbatim AGPL copies)"}


def fetch(rel_dir: str, name: str) -> dict:
    """Download one profile and store the *raw bytes* (verbatim upstream copy)."""
    url = f"{BASE}/{rel_dir}/{urllib.parse.quote(name)}"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req) as r:
        raw = r.read()
    out = FIXTURES / rel_dir / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    return json.loads(raw.decode("utf-8"))


def walk_chain(rel_dir: str, name: str, seen: set[str]) -> None:
    key = f"{rel_dir}/{name}"
    if key in seen:
        return
    seen.add(key)
    data = fetch(rel_dir, name)
    parent = data.get("inherits")
    if not parent:
        return
    # Multiple parents are semicolon-separated in Bambu Studio profiles.
    for parent_name in [p.strip() for p in str(parent).split(";") if p.strip()]:
        walk_chain(rel_dir, f"{parent_name}.json", seen)


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    seen: set[str] = set()
    # Machine chain (templates are plain files, no inheritance of their own).
    for name in [
        "Bambu Lab P2S.json",
        "Bambu Lab P2S 0.4 nozzle.json",
        "fdm_bbl_3dp_001_common.json",
        "Bambu Lab P2S 0.4 nozzle template machine_start_gcode.json",
        "Bambu Lab P2S 0.4 nozzle template machine_end_gcode.json",
        "Bambu Lab P2S 0.4 nozzle template layer_change_gcode.json",
        "Bambu Lab P2S 0.4 nozzle template time_lapse_gcode.json",
        "Bambu Lab P2S 0.4 nozzle template wrapping_detection_gcode.json",
        "Bambu Lab P2S 0.4 nozzle template change_filament_gcode.json",
    ]:
        walk_chain("machine", name, seen)
    # Default material and default process chains (from the machine profile).
    walk_chain("filament", "Bambu PLA Basic @BBL P2S.json", seen)
    walk_chain("process", "0.20mm Standard @BBL P2S.json", seen)

    entries = []
    for path in sorted(FIXTURES.rglob("*.json")):
        rel = path.relative_to(FIXTURES)
        entries.append({
            "path": str(rel),
            "url": f"{BASE}/{urllib.parse.quote(str(rel))}",
            "sha256": sha256(path),
        })

    manifest = {
        "source_repo": "https://github.com/bambulab/BambuStudio",
        "source_commit": COMMIT,
        "fetched_for": "Mesh Studio P2S compatibility spike (Phase 0)",
        "license": "AGPL-3.0 (verbatim unmodified copies; attribution required on redistribution)",
        "files": entries,
    }
    out = FIXTURES.parent / "MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"stored {len(entries)} files; manifest: {out}")


if __name__ == "__main__":
    main()
