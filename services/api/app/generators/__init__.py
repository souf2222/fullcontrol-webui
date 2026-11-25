"""Generator registry. Generators are trusted server-side code with an id
and a schema version they understand. Unknown ids or versions are rejected
by validation, never guessed at."""

from __future__ import annotations

from typing import Protocol

from app.schemas.project import Project

from .ripple_mesh import RippleMeshGenerator
from .smooth_spiral import SmoothSpiralGenerator


class Generator(Protocol):
    id: str
    version: int

    def validate(self, project: Project) -> list[str]: ...

    def generate(self, project: Project) -> list: ...


_REGISTRY: dict[str, Generator] = {
    gen.id: gen for gen in (SmoothSpiralGenerator(), RippleMeshGenerator())
}


def get_generator(generator_id: str) -> Generator | None:
    return _REGISTRY.get(generator_id)


def list_generators() -> list[dict]:
    return [{"id": g.id, "version": g.version} for g in _REGISTRY.values()]
