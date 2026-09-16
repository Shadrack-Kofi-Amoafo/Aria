"""Append-only learning evidence. Latest topic evidence determines current status."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol
from uuid import uuid4

from aria.storage import Database
from aria.validation import aware, nonempty


class EventKind(str, Enum):
    MASTERY = "mastery"
    DIFFICULTY = "difficulty"
    MISCONCEPTION = "misconception"
    STUDY = "study"


@dataclass(frozen=True)
class LearningEvent:
    id: str
    student_id: str
    kind: EventKind
    course_id: str
    topic: str
    details: str
    occurred_at: datetime


@dataclass(frozen=True)
class Message:
    id: str
    student_id: str
    session_id: str
    role: str
    content: str
    occurred_at: datetime


class MemoryRepository(Protocol):
    def record_event(self, student_id: str, kind: EventKind, course_id: str,
                     topic: str, details: str = "", *, at: datetime | None = None) -> LearningEvent: ...
    def history(self, student_id: str) -> list[LearningEvent]: ...
    def record_message(self, student_id: str, session_id: str, role: str,
                       content: str, *, at: datetime | None = None) -> Message: ...
    def conversations(self, student_id: str, session_id: str | None = None) -> list[Message]: ...


def timestamp(at: datetime | None) -> datetime:
    value = at if at is not None else datetime.now().astimezone()
    aware(value)
    return value.astimezone(timezone.utc)


class SQLiteMemoryRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record_event(self, student_id: str, kind: EventKind, course_id: str,
                     topic: str, details: str = "", *, at: datetime | None = None) -> LearningEvent:
        self.db.require_student(student_id)
        kind = EventKind(kind)
        nonempty(course_id, "course_id")
        nonempty(topic, "topic")
        if not isinstance(details, str):
            raise ValueError("details must be a string")
        event = LearningEvent(str(uuid4()), student_id, kind, course_id, topic, details, timestamp(at))
        with self.db.connection:
            self.db.connection.execute("INSERT INTO learning_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event.id, student_id, kind.value, course_id, topic, details, event.occurred_at.isoformat()))
        return event

    def history(self, student_id: str) -> list[LearningEvent]:
        self.db.require_student(student_id)
        rows = self.db.connection.execute(
            "SELECT * FROM learning_events WHERE student_id = ? ORDER BY occurred_at, rowid", (student_id,)
        ).fetchall()
        return [LearningEvent(r["id"], r["student_id"], EventKind(r["kind"]), r["course_id"],
                              r["topic"], r["details"], datetime.fromisoformat(r["occurred_at"])) for r in rows]

    def record_mastery(self, student_id: str, course_id: str, topic: str, details: str = "",
                       *, at: datetime | None = None) -> LearningEvent:
        return self.record_event(student_id, EventKind.MASTERY, course_id, topic, details, at=at)

    def record_difficulty(self, student_id: str, course_id: str, topic: str, details: str = "",
                          *, at: datetime | None = None) -> LearningEvent:
        return self.record_event(student_id, EventKind.DIFFICULTY, course_id, topic, details, at=at)

    def record_misconception(self, student_id: str, course_id: str, topic: str, details: str,
                             *, at: datetime | None = None) -> LearningEvent:
        return self.record_event(student_id, EventKind.MISCONCEPTION, course_id, topic, details, at=at)

    def record_study(self, student_id: str, course_id: str, topic: str, details: str = "",
                     *, at: datetime | None = None) -> LearningEvent:
        return self.record_event(student_id, EventKind.STUDY, course_id, topic, details, at=at)

    def record_message(self, student_id: str, session_id: str, role: str,
                       content: str, *, at: datetime | None = None) -> Message:
        self.db.require_student(student_id)
        nonempty(session_id, "session_id")
        nonempty(content, "content")
        if role not in {"user", "assistant", "system", "tool"}:
            raise ValueError("Invalid conversation role")
        message = Message(str(uuid4()), student_id, session_id, role, content, timestamp(at))
        with self.db.connection:
            self.db.connection.execute("INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?)",
                (message.id, student_id, session_id, role, content, message.occurred_at.isoformat()))
        return message

    def conversations(self, student_id: str, session_id: str | None = None) -> list[Message]:
        self.db.require_student(student_id)
        query = "SELECT * FROM conversations WHERE student_id = ?"
        args = [student_id]
        if session_id is not None:
            query += " AND session_id = ?"
            args.append(session_id)
        rows = self.db.connection.execute(query + " ORDER BY occurred_at, rowid", args).fetchall()
        return [Message(r["id"], r["student_id"], r["session_id"], r["role"], r["content"],
                        datetime.fromisoformat(r["occurred_at"])) for r in rows]


class StudentMemory:
    """Derived state, not a claim that a model objectively assessed mastery."""

    def __init__(self, repository: MemoryRepository) -> None:
        self.repository = repository

    def topic_status(self, student_id: str) -> dict[tuple[str, str], EventKind]:
        result = {}
        for event in self.repository.history(student_id):
            if event.kind != EventKind.STUDY:
                result[(event.course_id, event.topic)] = event.kind
        return result

    def topics_mastered(self, student_id: str) -> list[tuple[str, str]]:
        return [key for key, kind in self.topic_status(student_id).items() if kind == EventKind.MASTERY]

    def topics_struggled_with(self, student_id: str) -> list[tuple[str, str]]:
        return [key for key, kind in self.topic_status(student_id).items()
                if kind in {EventKind.DIFFICULTY, EventKind.MISCONCEPTION}]
