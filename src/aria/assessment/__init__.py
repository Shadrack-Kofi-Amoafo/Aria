"""Future quiz contract. Mastery evidence is stored by student_memory today."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class QuizQuestion:
    prompt: str
    expected_answer: str
    source_reference: str


class QuizProvider(Protocol):
    def create_quiz(self, course_id: str, topic: str, count: int = 3) -> list[QuizQuestion]: ...
