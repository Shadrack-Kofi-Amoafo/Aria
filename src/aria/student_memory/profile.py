"""Student-owned profile and local persistence."""
from dataclasses import asdict, dataclass, field
import json
from typing import Protocol

from aria.storage import Database
from aria.validation import nonempty, strings


@dataclass
class StudentProfile:
    student_id: str
    name: str
    preferred_name: str = ""
    courses: list[str] = field(default_factory=list)  # stable course IDs
    learning_preferences: list[str] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)

    def validate(self) -> None:
        nonempty(self.student_id, "student_id")
        nonempty(self.name, "name")
        if not isinstance(self.preferred_name, str):
            raise ValueError("preferred_name must be a string")
        for key in ("courses", "learning_preferences", "strengths", "weaknesses"):
            strings(getattr(self, key), key)
        if len(self.courses) != len(set(self.courses)):
            raise ValueError("course IDs must be unique")

    @property
    def display_name(self) -> str:
        return self.preferred_name.strip() or self.name


class ProfileRepository(Protocol):
    def save(self, profile: StudentProfile) -> None: ...
    def get(self, student_id: str) -> StudentProfile: ...
    def delete(self, student_id: str) -> None: ...


class SQLiteProfileRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def save(self, profile: StudentProfile) -> None:
        profile.validate()
        with self.db.connection:
            self.db.connection.execute(
                "INSERT INTO profiles VALUES (?, ?) ON CONFLICT(student_id) "
                "DO UPDATE SET payload = excluded.payload",
                (profile.student_id, json.dumps(asdict(profile))),
            )

    def get(self, student_id: str) -> StudentProfile:
        row = self.db.connection.execute(
            "SELECT payload FROM profiles WHERE student_id = ?", (student_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown student: {student_id}")
        return StudentProfile(**json.loads(row["payload"]))

    def delete(self, student_id: str) -> None:
        self.db.require_student(student_id)
        with self.db.connection:
            self.db.connection.execute("DELETE FROM profiles WHERE student_id = ?", (student_id,))
