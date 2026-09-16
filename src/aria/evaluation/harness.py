"""Behavioral evaluations usable with scripted or future local providers."""
from dataclasses import dataclass, field
from typing import Callable
from aria.agent.orchestrator import AgentResult


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    student_id: str
    conversation_id: str
    user_message: str
    expected_tools: tuple[str, ...] = ()
    expected_status: str = "final"
    expected_event_kinds: tuple[str, ...] = ()
    # Optional checks inspect authoritative application state, never model self-grading.
    checks: dict[str, Callable[[AgentResult, dict], bool]] = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluationResult:
    name: str
    passed: bool
    checks: dict[str, bool]
    error: str | None = None


class EvaluationHarness:
    def run_case(self, case, orchestrator, evidence):
        try:
            context = orchestrator.context_builder.build_context(case.student_id, case.conversation_id, case.user_message)
            before = {e.id for e in evidence.history(case.student_id)}
            result = orchestrator.run(case.student_id, case.conversation_id, case.user_message)
            created = [e for e in evidence.history(case.student_id) if e.id not in before]
            checks = {
                "tool_selection": tuple(t["name"] for t in result.trace) == case.expected_tools,
                "status": result.status == case.expected_status,
                "learning_events": tuple(e.kind.value for e in created) == case.expected_event_kinds,
                "context_scope": context["student_id"] == case.student_id and context["conversation_id"] == case.conversation_id,
            }
            for name, check in case.checks.items():
                checks[f"custom:{name}"] = check(result, context) is True
            return EvaluationResult(case.name, all(checks.values()), checks)
        except Exception as exc:
            return EvaluationResult(case.name, False, {}, type(exc).__name__)

    def summarize(self, results):
        results = list(results)
        return {"cases": len(results), "passed": sum(r.passed for r in results),
                "pass_rate": sum(r.passed for r in results) / len(results) if results else 0.0}
