"""Bounded synchronous orchestration. Model actions cannot grant write permission."""
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Callable
from aria.agent import ToolResult, json_value
from aria.model import FinalResponse, ModelInput, ModelProvider, ToolRequest
from aria.validation import nonempty


@dataclass(frozen=True)
class AgentResult:
    response: str
    status: str
    trace: tuple[dict, ...]
    model_calls: int


class Orchestrator:
    def __init__(self, context_builder, tools_factory, provider: ModelProvider, memory, *,
                 max_steps=6, max_tool_chars=12000, max_response_chars=8000,
                 confirm: Callable[[str, ToolRequest], bool] | None = None):
        for value in (max_steps, max_tool_chars, max_response_chars):
            if type(value) is not int or value < 1:
                raise ValueError("Orchestration limits must be positive integers")
        self.context_builder, self.tools_factory = context_builder, tools_factory
        self.provider, self.memory = provider, memory
        self.max_steps, self.max_tool_chars, self.max_response_chars = max_steps, max_tool_chars, max_response_chars
        self.confirm = confirm

    def run(self, student_id, conversation_id, user_message):
        context = self.context_builder.build_context(student_id, conversation_id, user_message)
        agent = self.tools_factory(student_id)
        self.memory.record_message(student_id, conversation_id, "user", user_message)
        trace, seen = [], set()

        def finish(text, status, steps):
            self.memory.record_message(student_id, conversation_id, "assistant", text)
            return AgentResult(text, status, tuple(deepcopy(trace)), steps)

        for step in range(1, self.max_steps + 1):
            try:
                action = self.provider.generate(ModelInput(deepcopy(context), tuple(deepcopy(trace))))
            except Exception:
                return finish("Model provider failed; no answer is available.", "provider_error", step)
            if isinstance(action, FinalResponse):
                if (not isinstance(action.text, str) or not action.text.strip() or
                        len(action.text) > self.max_response_chars or action.status not in {"final", "unavailable"}):
                    return finish("Invalid model response.", "invalid_action", step)
                # An unavailable tool cannot be turned into an apparent successful answer.
                if any(not item["result"]["ok"] for item in trace):
                    return finish("The requested operation could not be completed; see the tool result.", "unavailable", step)
                return finish(action.text, action.status, step)
            if not isinstance(action, ToolRequest):
                return finish("Invalid model action.", "invalid_action", step)
            try:
                nonempty(action.name, "tool name")
                nonempty(action.call_id, "call_id")
                if not isinstance(action.arguments, dict):
                    raise ValueError("arguments must be an object")
                encoded = json.dumps(action.arguments, allow_nan=False)
                if len(encoded) > self.max_tool_chars or len(action.name) > 200 or len(action.call_id) > 200:
                    raise ValueError("Tool request exceeds budget")
            except (TypeError, ValueError):
                return finish("Invalid model tool request.", "invalid_action", step)
            if action.call_id in seen:
                result = ToolResult(False, error="Duplicate tool call ID", code="duplicate_call")
            else:
                seen.add(action.call_id)
                confirmed = False
                if agent.requires_confirmation(action.name) and self.confirm:
                    # Pass a copy: callbacks cannot mutate the approved action after inspection.
                    try:
                        confirmed = self.confirm(student_id, deepcopy(action)) is True
                    except Exception:
                        confirmed = False
                result = agent.execute(action.name, action.arguments, confirmed=confirmed)
            result_data = json_value(result)
            if len(json.dumps(result_data)) > self.max_tool_chars:
                result_data = json_value(ToolResult(False, error="Tool result exceeds context budget", code="result_too_large"))
            entry = {"call_id": action.call_id, "name": action.name, "arguments": deepcopy(action.arguments), "result": result_data}
            trace.append(entry)
            self.memory.record_message(student_id, conversation_id, "tool", json.dumps(entry))
        return finish("Agent step limit reached without a final response.", "step_limit", self.max_steps)
