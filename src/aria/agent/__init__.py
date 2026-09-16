"""Explicit, student-scoped tool dispatch; no autonomous model loop yet."""
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, time
from enum import Enum
from typing import Any, Callable

from aria.student_memory import EventKind, MemoryRepository, ProfileRepository, StudentMemory
from aria.timetable import Timetable


def json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return json_value(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, type]
    handler: Callable[..., Any] | None = None
    mutates: bool = False


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None
    code: str | None = None


class ToolAgent:
    """Only registered tools run. Every mutation needs caller confirmation.

    Arguments are required, type checked, and cannot override the bound student.
    Future model output must go through this boundary, never eval or shell execution.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self._tools[tool.name] = tool

    def describe_tools(self) -> list[dict[str, Any]]:
        return [{"name": t.name, "description": t.description,
                 "parameters": {key: kind.__name__ for key, kind in t.parameters.items()},
                 "required": list(t.parameters), "available": t.handler is not None,
                 "mutates": t.mutates} for t in self._tools.values()]

    def execute(self, name: str, arguments: dict[str, Any] | None = None, *,
                confirmed: bool = False) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, error=f"Unknown tool: {name}", code="unknown_tool")
        if tool.handler is None:
            return ToolResult(False, error="Capability not configured in Phase 1", code="unavailable")
        args = {} if arguments is None else arguments
        if not isinstance(args, dict) or set(args) != set(tool.parameters):
            return ToolResult(False, error="Arguments must exactly match required parameters", code="invalid_arguments")
        if any(type(args[key]) is not kind for key, kind in tool.parameters.items()):
            return ToolResult(False, error="Invalid argument type", code="invalid_arguments")
        if tool.mutates and confirmed is not True:
            return ToolResult(False, error="Explicit confirmation required", code="confirmation_required")
        try:
            return ToolResult(True, data=json_value(tool.handler(**args)))
        except (ValueError, KeyError) as exc:
            return ToolResult(False, error=str(exc), code="domain_error")


def build_agent(student_id: str, profiles: ProfileRepository,
                memory: MemoryRepository, timetable: Timetable) -> ToolAgent:
    profiles.get(student_id)
    agent = ToolAgent()
    derived = StudentMemory(memory)

    def profile() -> dict[str, Any]:
        return {"profile": profiles.get(student_id), "learning_history": memory.history(student_id),
                "topics_mastered": derived.topics_mastered(student_id),
                "topics_struggled_with": derived.topics_struggled_with(student_id)}

    def record(kind: str, course_id: str, topic: str, details: str) -> Any:
        return memory.record_event(student_id, EventKind(kind), course_id, topic, details)

    agent.register(Tool("get_student_profile", "Profile and derived learning state", {}, profile))
    agent.register(Tool("get_learning_history", "Read learning evidence", {}, lambda: memory.history(student_id)))
    agent.register(Tool("get_conversations", "Read one conversation session", {"session_id": str},
                        lambda session_id: memory.conversations(student_id, session_id)))
    agent.register(Tool("record_message", "Store a conversation message locally",
                        {"session_id": str, "role": str, "content": str},
                        lambda session_id, role, content: memory.record_message(student_id, session_id, role, content), True))
    agent.register(Tool("record_learning_event", "Record mastery, difficulty, misconception, or study",
                        {"kind": str, "course_id": str, "topic": str, "details": str}, record, True))
    for name in ("get_todays_classes", "get_tomorrows_classes", "get_next_class"):
        method = getattr(timetable, name)
        agent.register(Tool(name, "Read local weekly timetable", {}, lambda method=method: method(student_id)))
    for name, description, params in (
        ("course_search", "Future local course catalog search", {"query": str}),
        ("slide_retrieval", "Future slide retrieval with provenance", {"course_id": str, "query": str}),
        ("create_quiz", "Future grounded quiz generation", {"course_id": str, "topic": str}),
        ("plan_lesson", "Future adaptive lesson planning", {"course_id": str, "topic": str}),
        ("create_diagram", "Future local diagrams/images", {"description": str}),
    ):
        agent.register(Tool(name, description, params))
    return agent
