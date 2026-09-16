"""Structured provider actions; future local adapters own parsing/tokenization."""
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol
from copy import deepcopy


@dataclass(frozen=True)
class ToolRequest:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = "call-1"


@dataclass(frozen=True)
class FinalResponse:
    text: str
    status: str = "final"  # final or unavailable


ModelAction = FinalResponse | ToolRequest


@dataclass(frozen=True)
class ModelInput:
    context: dict
    tool_results: tuple[dict, ...] = ()


class ModelProvider(Protocol):
    def generate(self, request: ModelInput) -> ModelAction: ...
    def stream(self, request: ModelInput) -> Iterator[ModelAction]:
        """Yield complete validated actions, not partial tool JSON/token fragments."""
        ...


class MockModel:
    """Script player for tests, not an intelligent responder. No keyword guessing."""
    def __init__(self, actions=()):
        self._script = deepcopy(tuple(actions))
        self._position = 0
        self.requests: list[ModelInput] = []

    def generate(self, request):
        self.requests.append(deepcopy(request))
        if self._position >= len(self._script):
            return FinalResponse("Mock script exhausted; no model inference is configured.", "unavailable")
        action = deepcopy(self._script[self._position])
        self._position += 1
        return action

    def stream(self, request):
        yield self.generate(request)

    def reset(self):
        self._position = 0
        self.requests.clear()
