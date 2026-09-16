"""Deterministic bounded context selection; no retrieval or model business logic."""
from dataclasses import dataclass
from datetime import datetime
import json
from aria.agent import json_value
from aria.timetable import Timetable
from aria.validation import aware, nonempty


@dataclass(frozen=True)
class ContextLimits:
    messages: int = 12
    events: int = 20
    concepts: int = 8
    facts: int = 12
    text_chars: int = 600
    user_chars: int = 4000
    total_chars: int = 48000

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise ValueError("Context limits must be positive integers")


class ContextBuilder:
    def __init__(self, profiles, memory, explicit, learning, timetable, tools_factory, *, clock=None, limits=None):
        self.profiles, self.memory, self.explicit = profiles, memory, explicit
        self.learning, self.timetable, self.tools_factory = learning, timetable, tools_factory
        self.clock = clock or timetable.clock
        self.limits = limits or ContextLimits()

    def build_context(self, student_id, conversation_id, user_message):
        nonempty(conversation_id, "conversation_id")
        nonempty(user_message, "user_message")
        if len(user_message) > self.limits.user_chars:
            raise ValueError("User message exceeds context input limit")
        now = self.clock()
        aware(now)
        now = now.astimezone(self.timetable.zone)
        tt = Timetable(self.timetable.repository, clock=lambda: now, zone=self.timetable.zone)
        states = self.learning.states(student_id)
        # Simple transparent relevance: exact concept ID mention, then recent interaction.
        states.sort(key=lambda s: (s.concept_id.casefold() in user_message.casefold(),
                                   s.last_interaction.isoformat() if s.last_interaction else "", s.course_id, s.concept_id), reverse=True)
        omitted = []

        def bound(value, path):
            value = json_value(value)
            if isinstance(value, str) and len(value) > self.limits.text_chars:
                omitted.append(path)
                return value[:self.limits.text_chars] + "…"
            if isinstance(value, list):
                if len(value) > 20:
                    omitted.append(path)
                return [bound(item, path) for item in value[:20]]
            if isinstance(value, dict):
                return {key: bound(item, f"{path}.{key}") for key, item in value.items()}
            return value

        context = {
            "student_id": student_id, "conversation_id": conversation_id,
            "user_message": user_message, "current_time": now.isoformat(),
            "student": bound(self.profiles.get(student_id), "student"),
            "memory_policy": "Profile metadata is student-managed; legacy evidence has unknown origin. Explicit facts and inferred state are separate.",
            "explicit_memory": bound(self.explicit.current(student_id)[-self.limits.facts:], "explicit_memory"),
            "conversation": bound(self.memory.conversations(student_id, conversation_id)[-self.limits.messages:], "conversation"),
            "learning_state": bound(states[:self.limits.concepts], "learning_state"),
            "learning_history": bound(self.memory.history(student_id)[-self.limits.events:], "learning_history"),
            "timetable": bound({"today": tt.get_todays_classes(student_id), "tomorrow": tt.get_tomorrows_classes(student_id),
                                "next": tt.get_next_class(student_id)}, "timetable"),
            "course_knowledge": {"available": False, "sources": [], "reason": "Course retrieval is not configured"},
            "tools": self.tools_factory(student_id).describe_tools(),
            "selection": {"limits": vars(self.limits), "truncated_fields": omitted,
                          "policy": "Recent bounded history; concept-ID match then recency. Not semantic retrieval."},
        }
        # Keep tool schemas intact; fail closed if mandatory metadata alone exceeds budget.
        for section in ("learning_history", "conversation", "learning_state", "explicit_memory"):
            while len(json.dumps(context, ensure_ascii=False)) > self.limits.total_chars and context[section]:
                context[section].pop(-1 if section == "learning_state" else 0)
                if section not in omitted:
                    omitted.append(section)
        if len(json.dumps(context, ensure_ascii=False)) > self.limits.total_chars:
            raise ValueError("Context budget too small for mandatory sections")
        return context
