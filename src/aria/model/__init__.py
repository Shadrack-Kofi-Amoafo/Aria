"""Backend-neutral local inference contract. No model downloads or loading on import."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class LanguageModel(Protocol):
    def generate(self, messages: list[ChatMessage], *, max_tokens: int = 512) -> str: ...

# Phase 1 LanguageModel remains the plain-text adapter port for compatibility.
from .provider import FinalResponse, MockModel, ModelAction, ModelInput, ModelProvider, ToolRequest
