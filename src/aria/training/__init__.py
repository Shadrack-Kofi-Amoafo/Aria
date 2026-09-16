"""Future optional training boundary. No fine-tuning or dataset creation today."""
from pathlib import Path
from typing import Protocol


class Trainer(Protocol):
    def train(self, dataset: Path, base_model: Path, output: Path) -> Path: ...
