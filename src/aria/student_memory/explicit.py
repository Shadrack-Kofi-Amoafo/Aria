"""Student-provided facts with their original statement/reference, never inferred."""
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4
from typing import Protocol
from aria.student_memory.memory import timestamp
from aria.validation import nonempty


@dataclass(frozen=True)
class ExplicitMemory:
    id: str
    student_id: str
    key: str
    value: str
    source: str
    occurred_at: datetime
    origin: str = "explicit"


class ExplicitMemoryRepository(Protocol):
    def record(self, student_id: str, key: str, value: str, source: str,
               *, at: datetime | None = None) -> ExplicitMemory: ...
    def history(self, student_id: str) -> list[ExplicitMemory]: ...
    def current(self, student_id: str) -> list[ExplicitMemory]: ...


class SQLiteExplicitMemoryRepository:
    def __init__(self, db):
        self.db = db

    def record(self, student_id, key, value, source, *, at=None):
        self.db.require_student(student_id)
        for name, item in (("key", key), ("value", value), ("source", source)):
            nonempty(item, name)
        fact = ExplicitMemory(str(uuid4()), student_id, key, value, source, timestamp(at))
        with self.db.connection:
            self.db.connection.execute("INSERT INTO explicit_memories VALUES (?, ?, ?, ?, ?, ?)",
                                       (fact.id, student_id, key, value, source, fact.occurred_at.isoformat()))
        return fact

    def history(self, student_id):
        self.db.require_student(student_id)
        return [ExplicitMemory(r["id"], student_id, r["key"], r["value"], r["source"], datetime.fromisoformat(r["occurred_at"]))
                for r in self.db.connection.execute(
                    "SELECT * FROM explicit_memories WHERE student_id = ? ORDER BY occurred_at, rowid", (student_id,))]

    def current(self, student_id):
        latest = {fact.key: fact for fact in self.history(student_id)}
        return [latest[key] for key in sorted(latest)]
