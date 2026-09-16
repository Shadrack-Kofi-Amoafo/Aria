"""Future local model evaluation boundary."""
from pathlib import Path
from typing import Protocol
from aria.model import LanguageModel


class Evaluator(Protocol):
    def evaluate(self, model: LanguageModel, cases: Path) -> dict[str, float]: ...

from .harness import EvaluationCase, EvaluationHarness, EvaluationResult
