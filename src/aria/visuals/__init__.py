"""Future local image/diagram provider; not implemented in Phase 1."""
from pathlib import Path
from typing import Protocol


class VisualProvider(Protocol):
    def create_diagram(self, description: str, output: Path) -> Path: ...
