"""Lesson planning port, to be connected to grounded course evidence later."""
from dataclasses import dataclass
from typing import Protocol
from aria.retrieval import SlideExcerpt


@dataclass(frozen=True)
class LessonPlan:
    topic: str
    steps: list[str]
    sources: list[SlideExcerpt]


class LessonPlanner(Protocol):
    def plan(self, student_id: str, course_id: str, topic: str) -> LessonPlan: ...
