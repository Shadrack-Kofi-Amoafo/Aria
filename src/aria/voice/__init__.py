"""Reserved offline voice ports only; no audio processing in Phase 1."""
from pathlib import Path
from typing import Protocol


class SpeechRecognizer(Protocol):
    def transcribe(self, audio: Path) -> str: ...


class SpeechSynthesizer(Protocol):
    def synthesize(self, text: str, output: Path) -> Path: ...
